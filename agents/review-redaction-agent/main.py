"""Review & Redaction coded agent — DisclosureFlow stage 4 (brief §3, §8.1, §8.3, §9, §10).

THE HERO. ONE single-purpose LangGraph graph that performs exactly one stage's
reasoning: given the `CandidateRecord`s surfaced by the search stage, it (1)
decides each record's RESPONSIVENESS and (2) for each responsive record proposes
zero or more redactions, each grounded in a specific PolicyProvider rule with a
source-grounded foreseeable-harm rationale and a filled, data-driven exemption
test. It returns a typed list of `RedactionProposal`s, each carrying a DERIVED
confidence signal (§8.1) and §8.3-validated at the boundary.

DEFAULT POSTURE IS DISCLOSURE (brief §3). FOIA runs on a presumption of openness:
the agent never withholds by default and never withholds on its own authority. It
must JUSTIFY every withholding against a specific PolicyProvider rule with a
source-grounded foreseeable-harm rationale; a human approves it DOWNSTREAM (this
thin Milestone-1 agent only EMITS proposals — the redaction-approval interrupt /
Action Center gate is Milestone-2 case-spine wiring, attached at the seam noted
below). "No exemption applies" is a LEGITIMATE NEGATIVE (§8.2): the record flows
forward responsive with zero proposals, NOT a failure.

HARD RULES honoured here (CLAUDE.md / build-prompt §1a):
  * This is NOT a supervisor. It never calls the other agents and contains no
    cross-stage sequencing/routing. The reject→revise loop, the batch-vs-full
    review routing, and the dead-letter routing all live in the Maestro case
    model, not in this graph.
  * It NEVER invents a rule_id, citation, or count. The closed set of rules it may
    ground a withholding in comes from the injected `PolicyProvider`
    (`get_applicable_rules(jurisdiction, record_type)`); the agent reasons over
    WHATEVER comes back (brief §7). The model is shown the allowed rules per
    record and may only choose among them; `citation`/`pack_id`/`pack_version`
    are copied from the seam, never from the model.
  * Confidence is DERIVED, never asked (§8.1). The model fills the test; a
    deterministic step computes confidence via `derive_confidence`. The model's
    output schema has no confidence field, so it can never set it.
  * `case_id` / `jurisdiction` are threaded from the inbound records onto every
    emitted `RedactionProposal` via the `IdentityEnvelope`; `pack_id`/
    `pack_version` come from the seam's `pack_metadata` (the `PackStamp`).

Graph shape (linear, single stage):
    START → review (LLM, Opus per §9) → assemble (derive confidence + §8.3 validate) → END

I/O contract:
    input  = GraphInput   (a list of locked `CandidateRecord`s + case_id/jurisdiction)
    output = ReviewResult  (a thin AGENT-LOCAL envelope: `{ proposals: list[RedactionProposal] }`)

Output-shape note: the §10 stage-4 contract is `RedactionProposal[]` (a list),
but a uipath/LangGraph OUTPUT schema must have an object root. `ReviewResult` is a
thin agent-local envelope that COMPOSES the locked `RedactionProposal` without
redefining it — the same decision the Custodian agent made with `SearchPlan`.
Maestro / the downstream human-review gate read `output.proposals` and route over
the real shared items. It is deliberately NOT a new shared pipeline contract (see
ASSUMPTIONS.md). `ReviewResult` ALSO echoes the per-record responsiveness
decisions (`reviewed`) so Maestro can mark non-responsive records and forward
clean records with no proposals — without that, a responsive record carrying zero
proposals would be indistinguishable from a non-responsive one.

§8.2/§8.3 failure posture: there is no structurally-valid-but-fake fallback. On
the instant the model returns, every proposal is §8.3-validated (`rule_id` ∈ the
PolicyProvider's returned set for that record's record_type; required test
elements populated; `record_ref` in the input candidate set). On the FIRST
validation failure the agent re-prompts ONCE with the specific violation; if the
second attempt still fails — or a permanent/auth precondition is missing (e.g. no
`ANTHROPIC_API_KEY`, §8.2 "permanent → no retry"; or no input records) — it
RAISES `ReviewUnrecoverableError`. Because the agent runs as a Maestro Service
Task, the raised failure makes Maestro pause the case and dead-letter it to a
human queue with full state preserved (§8.2). It NEVER emits an invalid or a
fabricated-but-valid proposal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from shared.contracts import (
    FEDERAL_FOIA,
    CandidateRecord,
    ConfidenceSignal,
    ExemptionTestResult,
    RedactionProposal,
    Rule,
    Span,
    TestElement,
    derive_confidence,
    validate_proposal,
)
from shared.seams import FederalFoiaPackProvider, PolicyProvider

from config import model_for, temperature_for


# ─────────────────────────────────────────────────────────────────────────────
# Unrecoverable-failure signal (§8.2)
# ─────────────────────────────────────────────────────────────────────────────


class ReviewUnrecoverableError(RuntimeError):
    """Raised when the Review & Redaction step cannot produce valid output.

    Per §8.2 this is the *unrecoverable* path: a permanent precondition is
    missing (e.g. `ANTHROPIC_API_KEY` not set — "permanent → no retry"; or no
    input records to review), or the LLM output is still §8.3-invalid after the
    one re-prompt. The agent runs as a Maestro Service Task, so raising (rather
    than emitting a fake but structurally-valid proposal) is what makes Maestro
    pause the case and route it to the human dead-letter queue with full state
    preserved. The message carries the `case_id` plus the specific violation /
    `record_ref` context for that queue.

    NOTE: an OVER-withholding (the model proposing a redaction that the §8.3
    validator rejects as ungrounded) is NOT silently dropped — it surfaces here
    as the re-prompt violation and, if still wrong, dead-letters to the human
    queue. The disclosure-default posture means a bad withholding is a failure to
    escalate, never a silently-accepted release-blocker.
    """

    def __init__(self, case_id: str, reason: str, record_ref: Optional[str] = None) -> None:
        self.case_id = case_id
        self.reason = reason
        self.record_ref = record_ref
        ctx = f" (record '{record_ref}')" if record_ref else ""
        super().__init__(
            f"Review & Redaction unrecoverable for case '{case_id}'{ctx}: {reason}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# PolicyProvider injection (the FIRST agent to use a seam + the pack data)
# ─────────────────────────────────────────────────────────────────────────────

# The federal-foia pack JSON lives OUTSIDE shared/ (it is data, not a .py module),
# so the vendor-then-pack build copies BOTH shared/ and policy-packs/ into this
# agent dir (see the Makefile / .gitignore). `FederalFoiaPackProvider` defaults to
# the repo-root pack via its own path math, which works for LOCAL runs, but on the
# packed robot the repo root does not exist — only the agent project dir does. So
# we resolve the pack relative to THIS file: prefer the vendored copy bundled
# alongside main.py (the robot path), and fall back to the canonical repo-root
# copy (the local-dev path) so a bare local run without a prior `make vendor`
# still works. Overridable via env for a different pack location/version.
_THIS_DIR = Path(__file__).resolve().parent
_VENDORED_PACK_DIR = _THIS_DIR / "policy-packs" / "federal-foia"
_REPO_ROOT_PACK_DIR = _THIS_DIR.parents[1] / "policy-packs" / "federal-foia"


def _resolve_pack_dir() -> Path:
    """Locate the federal-foia pack dir for the active runtime.

    Order: ``POLICY_PACK_DIR`` env override → the vendored copy bundled inside
    this agent (the robot path) → the canonical repo-root copy (local dev). The
    provider raises a clear FileNotFoundError if the chosen dir has no pack.json.
    """
    override = os.environ.get("POLICY_PACK_DIR")
    if override:
        return Path(override)
    if (_VENDORED_PACK_DIR / "pack.json").is_file():
        return _VENDORED_PACK_DIR
    return _REPO_ROOT_PACK_DIR


def _build_policy_provider() -> PolicyProvider:
    """Construct the demo PolicyProvider backing (brief §7).

    Injected as a dependency (the seam), not hardcoded rules. The demo backing is
    `FederalFoiaPackProvider`; a production swap (`RegistryPolicyProvider`) is a
    construction change here, not a logic change. Built lazily inside the review
    node so module import / `uipath init` schema introspection never needs the
    pack file present.
    """
    return FederalFoiaPackProvider(pack_dir=_resolve_pack_dir())


# ─────────────────────────────────────────────────────────────────────────────
# Graph input model
# ─────────────────────────────────────────────────────────────────────────────


class GraphInput(BaseModel):
    """Inbound payload for the Review & Redaction agent.

    Composes the list of locked `CandidateRecord`s the search stage surfaced PLUS
    the case identity. Maestro hands the Service Task exactly this shape after the
    record-store fan-out; the agent does NO RecordStore I/O itself (it stays a
    pure reasoning unit, independently runnable from a fixture). `case_id` /
    `jurisdiction` ride here so they thread onto every emitted proposal even
    though each record already carries its own identity (the top-level identity is
    authoritative for the proposals and is cross-checked against the records).

    Each `CandidateRecord.text` is what the model reads to find exemptions; a
    record with no `text` can only be judged on metadata (the model is told to
    mark it non-responsive-unknown / propose nothing rather than guess).
    """

    case_id: str = Field(description="Maestro case id; threaded onto every RedactionProposal.")
    jurisdiction: str = Field(
        default=FEDERAL_FOIA,
        description="Legal regime; only 'federal_foia' in MVP, passed as a real param.",
    )
    records: list[CandidateRecord] = Field(
        description="The candidate records to review (from the record-store fan-out)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal LLM structured-output schema (NOT a shared contract)
# ─────────────────────────────────────────────────────────────────────────────
#
# These are deliberately NARROWER than the locked contracts and carry NO identity
# fields, NO citation, NO pack stamp, and NO confidence — so the model can never
# fabricate a case_id, invent a citation, or set its own confidence. The agent
# fills those deterministically from the seam / the input in `assemble_node`.


class _TestElementDraft(BaseModel):
    """The model's finding for one element of a rule's legal test (§8.1b, §8.3)."""

    value: str = Field(description="The finding for this element (e.g. 'pre-decisional').")
    evidence: str = Field(
        description="Source-grounded support quoted/paraphrased from THIS record's text."
    )
    hedged: bool = Field(
        default=False,
        description="True ONLY if you are genuinely uncertain; an honest hedge routes to full human review.",
    )


class _RedactionDraft(BaseModel):
    """One proposed withholding from the model for a single record.

    Carries only what the model decides: which allowed rule grounds it, the
    verbatim `quote` to redact (the agent LOCATES it in the record text and
    derives the char span — the model never computes offsets), the foreseeable-
    harm rationale, and the filled test elements (keyed by the rule's
    `required_test_elements`). No span offsets / citation / pack / identity /
    confidence — those are derived from the record + the seam in `assemble_node`.
    """

    rule_id: str = Field(
        description="Rule id grounding this withholding. MUST be one of the allowed rule ids shown for this record."
    )
    quote: str = Field(
        description=(
            "The EXACT verbatim substring of the record text to redact — copied character-for-character "
            "from the record (the agent locates it; do NOT compute character offsets). Redact the NARROWEST "
            "substring that removes the harm (a name, an identifier, a sentence), never a whole document."
        )
    )
    rationale: str = Field(
        description="Source-grounded, FORESEEABLE-HARM rationale: what specific harm disclosure of THIS text would cause."
    )
    test_elements: dict[str, _TestElementDraft] = Field(
        default_factory=dict,
        description="element_name -> finding. Fill EVERY required element for the chosen rule; leave none blank/hedged unless genuinely uncertain.",
    )


class _RecordReviewDraft(BaseModel):
    """The model's review of ONE record."""

    record_ref: str = Field(description="The record being reviewed. MUST be one of the input record refs.")
    is_responsive: bool = Field(
        description="True if this record is within the scope of the request and should be processed for release."
    )
    redactions: list[_RedactionDraft] = Field(
        default_factory=list,
        description="Proposed withholdings for this record. EMPTY when no exemption applies (the disclosure default).",
    )


class _ReviewDraft(BaseModel):
    """Constrained shape the reviewing LLM must return: one review per record."""

    reviews: list[_RecordReviewDraft] = Field(
        description="One review per input record (responsiveness + zero-or-more proposed redactions)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Output models (thin, agent-local; compose the locked RedactionProposal)
# ─────────────────────────────────────────────────────────────────────────────


class RecordReview(BaseModel):
    """Per-record responsiveness decision echoed on the output (agent-local).

    Maestro reads this to mark non-responsive records and to distinguish a
    responsive record carrying ZERO proposals (a clean, fully-disclosable record —
    the disclosure default) from a non-responsive one. It is NOT a new pipeline
    contract: it only surfaces the `is_responsive` decision the agent made about
    each input record (the locked `CandidateRecord.is_responsive` field is what
    Maestro ultimately stamps; this echo carries the decision out of the graph).
    """

    record_ref: str = Field(description="The reviewed record.")
    is_responsive: bool = Field(description="The agent's responsiveness decision for this record.")
    proposal_count: int = Field(ge=0, description="How many redactions were proposed for this record.")


class ReviewResult(BaseModel):
    """Thin agent-local OUTPUT envelope wrapping the §10 `RedactionProposal[]` list.

    The §10 stage-4 contract is `RedactionProposal[]`, but a uipath/LangGraph
    OUTPUT schema needs an object root — so the graph emits
    `ReviewResult{proposals:[...], reviewed:[...]}`. `proposals` COMPOSES the
    locked, shared `RedactionProposal` (it does not redefine it). `reviewed`
    echoes the per-record responsiveness decisions so Maestro can route clean /
    non-responsive records. Deliberately agent-local, not a new shared pipeline
    contract (see ASSUMPTIONS.md), mirroring the Custodian agent's `SearchPlan`.
    """

    proposals: list[RedactionProposal] = Field(
        description="Every proposed withholding across all records (each §8.3-validated, confidence DERIVED)."
    )
    reviewed: list[RecordReview] = Field(
        description="Per-record responsiveness decisions (so Maestro can forward clean / non-responsive records)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graph state
# ─────────────────────────────────────────────────────────────────────────────


class State(BaseModel):
    """Internal state flowing between nodes."""

    case_id: str
    jurisdiction: str = FEDERAL_FOIA
    records: list[CandidateRecord] = Field(default_factory=list)

    # Produced by the review node.
    draft: Optional[_ReviewDraft] = None


# ─────────────────────────────────────────────────────────────────────────────
# Prompting
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the Review & Redaction officer-assistant for a U.S. federal FOIA \
(Freedom of Information Act) request-processing system. FOIA runs on a PRESUMPTION OF OPENNESS \
and a FORESEEABLE-HARM standard: your DEFAULT is to DISCLOSE. You may only propose withholding \
text when you can justify it against a SPECIFIC allowed exemption rule with a source-grounded, \
foreseeable-harm rationale. You never withhold "to be safe." A human records officer approves \
every withholding AFTER you — you propose, you do not decide.

For EACH record you are given, do two things:

1. RESPONSIVENESS. Decide whether the record is within the scope of the request \
(is_responsive). A record that is off-topic, or that a "wrong_docs" custodian returned by \
mistake, is NOT responsive — mark is_responsive=false and propose no redactions for it. If a \
record has no readable text, you cannot assess it: mark is_responsive=false.

2. REDACTIONS (only for responsive records). Propose ZERO OR MORE withholdings. ZERO is the \
common, correct answer for a clean record — an empty list means "fully disclosable," which is \
the disclosure default. For each withholding you DO propose:
   - rule_id: choose ONE of the ALLOWED rules shown for that record. Use ONLY the rule ids \
listed. Never invent a rule id or a citation.
   - quote: the EXACT verbatim substring of THIS record's text to redact, copied \
character-for-character from the record. Do NOT compute character offsets — the system locates \
your quote in the text. The quote MUST appear in the record exactly once; if the same harmful \
text appears more than once, include enough surrounding context to make the quote unique. \
Redact the NARROWEST substring that removes the harm — a name, an identifier, or a sentence — \
never a whole document when a name or a sentence suffices.
   - rationale: the FORESEEABLE HARM — what specific, identifiable harm would disclosing THIS \
text reasonably be expected to cause? Ground it in the record's actual content, not a generic \
recital of the exemption.
   - test_elements: fill EVERY required element listed for the chosen rule, each with a \
`value` (your finding) and `evidence` (grounded in this record's text). Set hedged=true ONLY \
if you are genuinely uncertain about an element — an honest hedge sends the proposal to closer \
human review, which is correct; a false confident answer is not. Do not pad: if an element is \
not actually supported by the record, do not fabricate evidence for it.

Common federal exemptions you will see (use the rule id EXACTLY as listed for the record):
- Deliberative-process material (b5): pre-decisional, deliberative recommendations/opinions in \
internal memos — NOT purely factual material and NOT final decisions.
- Personal privacy (b6): names, contact details, and similar personal information in personnel/ \
medical/"similar" files whose disclosure would be a clearly unwarranted privacy invasion.
- Law-enforcement personal privacy (b7c): personal information in records compiled for law- \
enforcement purposes.

Return ONLY the structured object: one review per record, in the same order."""


def _record_block(record: CandidateRecord, allowed: list[Rule]) -> str:
    """Render one record + its allowed rules for the prompt."""
    rules_lines = []
    for r in allowed:
        elems = ", ".join(r.required_test_elements)
        rules_lines.append(
            f"    - rule_id={r.id!r} [{r.test}] {r.citation}: {r.text}\n"
            f"        required_test_elements: [{elems}]"
        )
    rules_block = "\n".join(rules_lines) if rules_lines else "    (no exemption rules apply to this record type)"
    text = record.text if record.text else "(no text available — cannot assess; mark non-responsive)"
    return (
        f"  record_ref: {record.record_ref}\n"
        f"  department: {record.department}\n"
        f"  record_type: {record.record_type}\n"
        f"  ALLOWED rules for this record (choose rule_id ONLY from these):\n{rules_block}\n"
        f"  text:\n  \"\"\"\n{text}\n  \"\"\""
    )


def _human_prompt(state: State, allowed_by_ref: dict[str, list[Rule]]) -> str:
    blocks = []
    for rec in state.records:
        blocks.append(_record_block(rec, allowed_by_ref.get(rec.record_ref, [])))
    body = "\n\n".join(blocks)
    return (
        f"Case id: {state.case_id}\n"
        f"Jurisdiction: {state.jurisdiction}\n"
        f"Review the following {len(state.records)} record(s). For EACH, decide responsiveness "
        f"and propose redactions ONLY where a listed rule and a foreseeable harm both genuinely "
        f"apply (zero is the common, correct answer):\n\n"
        f"{body}"
    )


# Name of the Orchestrator asset holding the Anthropic API key (brief §13: the
# key lives as an Orchestrator secret/credential asset, never hardcoded). On the
# serverless robot the key is NOT in os.environ by default, so we fall back to
# reading this asset via the UiPath SDK (which auto-authenticates inside a job).
# Overridable via env so a different tenant/asset name needs no code change.
ANTHROPIC_KEY_ASSET = os.environ.get(
    "ANTHROPIC_KEY_ASSET_NAME", "DisclosureFlow_AnthropicApiKey"
)


def _resolve_anthropic_key() -> Optional[str]:
    """Resolve the Anthropic API key for direct-Anthropic calls (brief §13).

    Resolution order (so the SAME code path works locally and on the robot):
      1. ``os.environ['ANTHROPIC_API_KEY']`` — local dev (.env) and the robot
         path where the process env var is bound to the asset via the
         ``ANTHROPIC_API_KEY=%ASSETS/<name>%`` Orchestrator env reference.
      2. UiPath Orchestrator asset ``ANTHROPIC_KEY_ASSET`` read through the SDK
         — the serverless-robot fallback when the env var is not bound. The SDK
         auto-authenticates from the in-job robot context; ``retrieve_secret``
         (robot-scoped) is tried first, then ``retrieve`` (direct value). This is
         the CONFIRMED-WORKING robot key path (see ASSUMPTIONS.md).

    Returns ``None`` if no key can be obtained — the caller treats that as the
    §8.2 permanent precondition failure and raises. Never logs the key value.
    """
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key

    # Robot fallback: read the key from the Orchestrator asset. Imported lazily so
    # local runs and `uipath init` introspection never require the SDK at import.
    try:
        from uipath import UiPath  # type: ignore
    except Exception:
        try:
            from uipath.platform import UiPath  # type: ignore
        except Exception:
            return None
    try:
        sdk = UiPath()
        for getter in ("retrieve_secret", "retrieve"):
            try:
                value = getattr(sdk.assets, getter)(name=ANTHROPIC_KEY_ASSET)
            except Exception:
                continue
            if value is None:
                continue
            resolved = getattr(value, "value", value)
            if isinstance(resolved, str) and resolved:
                return resolved
    except Exception:
        return None
    return None


def _build_llm(step: str, api_key: str):
    """Construct the Claude client for a step (direct Anthropic per brief §13).

    Lazily imported and constructed so module import (and `uipath init` schema
    introspection) never requires the SDK/key. Model is resolved via
    `model_for(step)` — never hardcoded. The API key is resolved by the caller
    (env → Orchestrator asset) and passed in explicitly so the robot path works
    even when ``ANTHROPIC_API_KEY`` is not in the process environment.

    There is NO call-time model fallback: `model_for(step)` already applied the
    only fallback (env → §9 default → ``DEFAULT_MODEL``) at config time. If the
    resolved model id is unavailable, the Anthropic client raises at invoke time
    and `review_node` treats it as a transport/parse error (one raw-JSON retry on
    the SAME model, then raise). ``temperature`` is OMITTED unless
    `temperature_for` returns a value — Opus 4.8 (the §9 model here) rejects it.
    """
    from langchain_anthropic import ChatAnthropic

    kwargs = dict(
        model=model_for(step),
        max_tokens=4096,
        timeout=120,
        api_key=api_key,
    )
    temperature = temperature_for(step)
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Nodes (async per the required-structure conventions)
# ─────────────────────────────────────────────────────────────────────────────


def _allowed_rules_by_record(
    records: list[CandidateRecord], jurisdiction: str, provider: PolicyProvider
) -> dict[str, list[Rule]]:
    """Build {record_ref -> allowed Rule[]} via the PolicyProvider seam (§7, §8.3).

    Calls `get_applicable_rules(jurisdiction, record_type)` per record so the
    allowed set is keyed off the record's own type (the seam may filter by type
    in a future pack). This map is BOTH what the model is shown (so it can only
    choose among allowed rules) AND the closed set the §8.3 validator checks
    `rule_id` membership against — one source of truth, so the prompt and the
    validation can never diverge.
    """
    by_ref: dict[str, list[Rule]] = {}
    for rec in records:
        by_ref[rec.record_ref] = provider.get_applicable_rules(jurisdiction, rec.record_type)
    return by_ref


async def review_node(state: State) -> State:
    """Responsiveness + grounded exemption classification (Opus per §9).

    §8.3 posture: the model returns the constrained `_ReviewDraft`; on a
    parse/validation failure we RE-PROMPT ONCE with the specific violation. If the
    second attempt still fails we RAISE `ReviewUnrecoverableError` (§8.2
    unrecoverable). Structured output is requested via the model's native schema
    binding, with a raw-JSON fallback for providers that don't honour it. The
    PolicyProvider seam is consulted here only to BUILD THE PROMPT (show the
    allowed rules); the authoritative §8.3 membership check runs again in
    `assemble_node` against the same map.
    """
    if not state.records:
        # Nothing to review: a permanent precondition failure (§8.2). The records
        # are injected by Maestro after the fan-out; an empty set means there is
        # no work for this stage. Raise → human queue. (A search that found no
        # records is itself a legitimate-negative the case model handles upstream;
        # it should not invoke this Service Task at all.)
        raise ReviewUnrecoverableError(
            state.case_id,
            "no records were provided to review; the candidate set is empty "
            "(permanent, §8.2) — the review step has nothing to act on.",
        )

    api_key = _resolve_anthropic_key()
    if not api_key:
        raise ReviewUnrecoverableError(
            state.case_id,
            "ANTHROPIC_API_KEY unavailable (not in env and Orchestrator asset "
            f"'{ANTHROPIC_KEY_ASSET}' not readable); the review LLM step could not "
            "run (permanent, §8.2).",
        )

    provider = _build_policy_provider()
    allowed_by_ref = _allowed_rules_by_record(state.records, state.jurisdiction, provider)

    llm = _build_llm("review", api_key)
    structured = llm.with_structured_output(_ReviewDraft)
    base_messages = [
        SystemMessage(_SYSTEM_PROMPT),
        HumanMessage(_human_prompt(state, allowed_by_ref)),
    ]

    last_error: Optional[str] = None
    for attempt in range(2):  # one initial try + one re-prompt (§8.3)
        messages = list(base_messages)
        if attempt == 1 and last_error is not None:
            messages.append(
                HumanMessage(
                    "Your previous response did not satisfy the required schema or grounding "
                    f"constraints: {last_error}. Return ONLY a valid object. Every proposed "
                    "redaction must use a rule_id from the ALLOWED rules listed for that "
                    "record, target an in-scope record_ref, and fill every required test "
                    "element for the chosen rule. If a withholding cannot be grounded, do not "
                    "propose it (disclosure is the default)."
                )
            )
        try:
            draft = await structured.ainvoke(messages)
            if not isinstance(draft, _ReviewDraft):
                draft = _ReviewDraft.model_validate(draft)
            err = _draft_validation_error(draft, state, allowed_by_ref)
            if err is not None:
                last_error = err
                continue
            return state.model_copy(update={"draft": draft})
        except ValidationError as exc:
            last_error = "; ".join(
                f"{'/'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
        except Exception as exc:  # transport/parse error: try the raw-JSON fallback once
            last_error = str(exc)
            parsed = await _raw_json_fallback(llm, base_messages)
            if parsed is not None:
                fallback_err = _draft_validation_error(parsed, state, allowed_by_ref)
                if fallback_err is None:
                    return state.model_copy(update={"draft": parsed})
                # The fallback parsed but is ungrounded: carry ITS specific §8.3
                # violation into the one re-prompt (not the transport str), so the
                # corrective re-prompt actually addresses the grounding problem
                # rather than discarding it. Still fails closed to raise if the
                # next attempt is also invalid.
                last_error = fallback_err

    raise ReviewUnrecoverableError(
        state.case_id,
        f"the review LLM could not produce valid, grounded proposals after one §8.3 "
        f"re-prompt: {last_error}",
    )


def _draft_validation_error(
    draft: _ReviewDraft, state: State, allowed_by_ref: dict[str, list[Rule]]
) -> Optional[str]:
    """§8.3 boundary validation of the model draft (returns a re-prompt string or None).

    Converts each proposed redaction into a real `RedactionProposal` (the same
    locked contract the agent will emit) and runs the SHARED `validate_proposal`
    against the per-record allowed rule set and the in-scope record refs — so the
    boundary check is exactly the locked §8.3 validator, not a re-implementation.
    Also checks each review's `record_ref` is an input record and the span quote
    matches the record text. Returns the FIRST violation as a human-readable
    string (drives the one re-prompt), or None if the whole draft passes.
    """
    in_scope = {rec.record_ref for rec in state.records}
    records_by_ref = {rec.record_ref: rec for rec in state.records}

    for review in draft.reviews:
        if review.record_ref not in in_scope:
            return (
                f"review references record_ref {review.record_ref!r}, which is not one of the "
                f"input records {sorted(in_scope)}."
            )
        if not review.is_responsive and review.redactions:
            return (
                f"record {review.record_ref!r} is marked non-responsive but carries "
                f"{len(review.redactions)} redaction(s); a non-responsive record needs no redactions."
            )
        rec = records_by_ref[review.record_ref]
        allowed = allowed_by_ref.get(review.record_ref, [])
        for r in review.redactions:
            # Build the locked proposal (a placeholder confidence; the §8.3
            # validator does not read confidence — it is DERIVED later). This also
            # locates the quote in the record text and DERIVES the span, raising a
            # grounding ValueError if the quote is absent/ambiguous.
            try:
                proposal = _draft_to_proposal(r, rec, state, allowed, _placeholder_confidence())
            except (ValidationError, ValueError) as exc:
                return f"record {review.record_ref!r}: proposed redaction failed to build: {exc}"
            violations = validate_proposal(proposal, allowed, in_scope)
            if violations:
                v = violations[0]
                return (
                    f"record {review.record_ref!r}: §8.3 violation [{v.kind}] for rule "
                    f"{v.rule_id!r}: {v.detail}."
                )
    return None


def _locate_span(draft: _RedactionDraft, record: CandidateRecord) -> tuple[int, int]:
    """Locate the model's `quote` in the record text → DERIVE the char span.

    The model supplies the verbatim `quote`, NOT character offsets — LLMs are
    unreliable at character arithmetic (a live run had the model pick the right
    substring but the wrong offsets). Grounding stays strict: the quote must
    actually appear in the record text, and must appear EXACTLY ONCE so the
    redaction is unambiguous (the prompt tells the model to add context to
    disambiguate a repeated phrase). The span is then `(idx, idx+len(quote))`,
    guaranteeing `record.text[start:end] == quote` by construction — the
    integrity the §8.3/audit story needs, without trusting model-computed offsets.

    Raises ValueError (a grounding failure) if the record has no text, the quote
    is empty, the quote is absent, or the quote is ambiguous (appears >1×).
    """
    if record.text is None:
        raise ValueError("the record has no text to ground a redaction span in.")
    quote = draft.quote
    if not quote:
        raise ValueError("the redaction quote is empty; nothing to locate/redact.")
    first = record.text.find(quote)
    if first == -1:
        raise ValueError(
            f"the redaction quote {quote!r} does not appear in the record text; "
            "copy the substring to redact character-for-character from the record."
        )
    if record.text.find(quote, first + 1) != -1:
        raise ValueError(
            f"the redaction quote {quote!r} appears more than once in the record text; "
            "include enough surrounding context to make the quote unique."
        )
    return first, first + len(quote)


async def _raw_json_fallback(llm, base_messages) -> Optional[_ReviewDraft]:
    """Ask for raw JSON and parse it into `_ReviewDraft`; None if it still fails.

    Reached only from the broad `except` in `review_node` — a transport/parse
    error from the native structured-output binding, not a schema/constraint
    failure. Re-sends the original prompt with an explicit JSON-shape instruction.
    """
    try:
        instruction = HumanMessage(
            "Respond with ONLY a JSON object of shape: "
            '{"reviews": [{"record_ref": str, "is_responsive": bool, "redactions": '
            '[{"rule_id": str, "quote": str, "rationale": str, '
            '"test_elements": {"<element_name>": {"value": str, "evidence": str, '
            '"hedged": bool}}}]}]}. Use rule_id values only from the allowed rules shown; '
            "`quote` is the verbatim substring of the record text to redact (no offsets)."
        )
        resp = await llm.ainvoke(base_messages + [instruction])
        content = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return None
        return _ReviewDraft.model_validate_json(content[start : end + 1])
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Assemble (derive confidence + final §8.3 validation)
# ─────────────────────────────────────────────────────────────────────────────


def _placeholder_confidence() -> ConfidenceSignal:
    """A throwaway confidence used only to build a proposal for §8.3 pre-validation.

    The §8.3 validator never reads `confidence` — but `RedactionProposal` requires
    one to construct. In `assemble_node` the REAL confidence is DERIVED via
    `derive_confidence` and replaces this; this value never reaches the output.
    """
    return ConfidenceSignal(level="low", derivation="incomplete_test_elements")


def _draft_to_proposal(
    draft: _RedactionDraft,
    record: CandidateRecord,
    state: State,
    allowed: list[Rule],
    confidence: ConfidenceSignal,
) -> RedactionProposal:
    """Build a locked `RedactionProposal` from a model draft + seam-derived fields.

    The model supplies rule_id / quote / rationale / test_elements; the span is
    DERIVED by locating the quote in the record text (`_locate_span`), never from
    model-computed offsets. Everything that grounds the withholding in a REAL rule
    is copied from the PolicyProvider rule (citation), and the pack stamp is set in
    `assemble_node`. Raises ValueError if the rule_id is not in the allowed set
    (so a citation can never be invented) or the quote can't be located uniquely.
    Constructing the model runs its locked Pydantic validators (§8.3 boundary).
    """
    rule = next((r for r in allowed if r.id == draft.rule_id), None)
    if rule is None:
        raise ValueError(
            f"rule_id {draft.rule_id!r} is not in the allowed set "
            f"{sorted(r.id for r in allowed)} for record {record.record_ref!r}."
        )
    test_result = ExemptionTestResult(
        test=rule.test,
        elements={
            name: TestElement(value=el.value, evidence=el.evidence, hedged=el.hedged)
            for name, el in draft.test_elements.items()
        },
    )
    start, end = _locate_span(draft, record)  # DERIVED span; quote == text[start:end] by construction
    span = Span(
        record_ref=record.record_ref,
        start=start,
        end=end,
        unit="char",
        quote=draft.quote,
    )
    return RedactionProposal(
        case_id=state.case_id,
        jurisdiction=state.jurisdiction,
        pack_id="__pending__",  # replaced in assemble_node from pack_metadata
        pack_version="__pending__",
        record_ref=record.record_ref,
        span=span,
        rule_id=rule.id,
        citation=rule.citation,  # copied from the seam, never the model
        rationale=draft.rationale,
        test_result=test_result,
        confidence=confidence,
    )


async def assemble_node(state: State) -> ReviewResult:
    """Assemble + boundary-validate the typed proposals; DERIVE confidence (§8.1, §8.3).

    For each proposed redaction: rebuild the locked `RedactionProposal`, stamp the
    pack (`pack_id`/`pack_version` from `pack_metadata` — never the model), DERIVE
    confidence via `derive_confidence(rule, test_result)` (§8.1 — never a model
    field), and run the FINAL §8.3 `validate_proposal` against the per-record
    allowed set + in-scope refs. Any surviving violation here (the draft passed
    `review_node` but a contract failed) RAISES `ReviewUnrecoverableError` so
    Maestro dead-letters to a human (§8.2) — no faked-but-valid placeholder is
    emitted. `reviewed` echoes each record's responsiveness so Maestro can forward
    clean / non-responsive records.
    """
    if state.draft is None:  # defensive: review_node either set draft or already raised
        raise ReviewUnrecoverableError(
            state.case_id, "no review draft was produced by the review step."
        )

    provider = _build_policy_provider()
    pack_meta = provider.pack_metadata(state.jurisdiction)
    allowed_by_ref = _allowed_rules_by_record(state.records, state.jurisdiction, provider)
    in_scope = {rec.record_ref for rec in state.records}
    records_by_ref = {rec.record_ref: rec for rec in state.records}

    proposals: list[RedactionProposal] = []
    reviewed: list[RecordReview] = []

    for review in state.draft.reviews:
        rec = records_by_ref.get(review.record_ref)
        if rec is None:  # defensive: review_node already enforced this
            raise ReviewUnrecoverableError(
                state.case_id,
                f"review references record_ref {review.record_ref!r}, not in the input set.",
                record_ref=review.record_ref,
            )
        allowed = allowed_by_ref.get(review.record_ref, [])
        record_proposals: list[RedactionProposal] = []
        for r in review.redactions:
            rule = next((rl for rl in allowed if rl.id == r.rule_id), None)
            if rule is None:
                # Off-allowed-set rule slipped past the re-prompt: unrecoverable (§8.2).
                raise ReviewUnrecoverableError(
                    state.case_id,
                    f"proposed rule_id {r.rule_id!r} is not in the allowed set "
                    f"{sorted(rl.id for rl in allowed)} for this record's type "
                    f"{rec.record_type!r}; a withholding cannot be grounded in an "
                    "invented rule.",
                    record_ref=review.record_ref,
                )
            # Build with placeholder confidence, then DERIVE the real one (§8.1).
            try:
                proposal = _draft_to_proposal(
                    r, rec, state, allowed, _placeholder_confidence()
                )
            except (ValidationError, ValueError) as exc:
                raise ReviewUnrecoverableError(
                    state.case_id,
                    f"a proposed redaction failed boundary validation: {exc}",
                    record_ref=review.record_ref,
                ) from exc

            # DERIVE confidence (§8.1): balancing rules (b6/b7c) ALWAYS low/full
            # review; any hedged/blank required element → low; else high. The
            # model NEVER sets this — self_consistency stays None (Milestone-5).
            confidence = derive_confidence(rule, proposal.test_result, self_consistency=None)
            # Stamp the pack from the seam and attach the DERIVED confidence.
            proposal = proposal.model_copy(
                update={
                    "pack_id": pack_meta.pack_id,
                    "pack_version": pack_meta.version,
                    "confidence": confidence,
                }
            )

            # FINAL §8.3 boundary validation against the locked shared validator.
            violations = validate_proposal(proposal, allowed, in_scope)
            if violations:
                v = violations[0]
                raise ReviewUnrecoverableError(
                    state.case_id,
                    f"§8.3 boundary validation failed after assembly [{v.kind}] for "
                    f"rule {v.rule_id!r}: {v.detail}.",
                    record_ref=review.record_ref,
                )
            record_proposals.append(proposal)

        proposals.extend(record_proposals)
        reviewed.append(
            RecordReview(
                record_ref=review.record_ref,
                is_responsive=review.is_responsive,
                proposal_count=len(record_proposals),
            )
        )

    return ReviewResult(proposals=proposals, reviewed=reviewed)


# ─────────────────────────────────────────────────────────────────────────────
# Graph wiring
#
# SEAM for Milestone-2 (the redaction-approval HITL): the §5 redaction-approval
# gate is a LangGraph INTERRUPT inside THIS agent — the officer's accept/reject/
# edit must re-enter the graph to drive the revise loop. That interrupt + the
# corrections-memory lookup (advisory, §7) attach as nodes AFTER `assemble` in
# Milestone 2; this thin agent stops at emitting validated proposals. Adding the
# interrupt is an additive node + edge, not a reshape of these two nodes.
# ─────────────────────────────────────────────────────────────────────────────

builder = StateGraph(State, input=GraphInput, output=ReviewResult)
builder.add_node("review", review_node)
builder.add_node("assemble", assemble_node)
builder.add_edge(START, "review")
builder.add_edge("review", "assemble")
builder.add_edge("assemble", END)

graph = builder.compile()
