"""Intake/Scoping coded agent — DisclosureFlow stage 1-2 (brief §3, §5, §9, §10).

ONE single-purpose LangGraph graph that performs exactly one stage's reasoning:
it interprets a raw `Request`, detects ambiguity, proposes a scope + triage
`track`, and — when the scope is too vague to search — drafts a requester-facing
*optional* narrowing suggestion (the §5 clarification loop). It returns typed
JSON validated at the agent boundary (§8.3 posture).

HARD RULES honoured here (CLAUDE.md / build-prompt §1a):
  * This is NOT a supervisor. It never calls the other agents and contains no
    cross-stage sequencing/routing — `is_vague` is a *signal* the Maestro case
    model reads to drive the clarification branch; the multi-turn re-scope loop
    lives in Maestro, not in this graph.
  * It NEVER silently narrows. The emitted `ScopedRequest` always carries the
    original requester scope (subject/record_types/etc.); any narrowing lives
    ONLY inside the `ClarificationDraft.suggested_narrowing`, framed as optional
    ("I can fill this narrower version faster — or keep your original").
  * `jurisdiction` and `case_id` are threaded from the inbound `Request` context
    onto the `ScopedRequest` via the `IdentityEnvelope` (passed from day one).

Graph shape (linear, single stage):
    START → scope (LLM, Sonnet per §9) → assemble (typed-validate, §8.3) → END

I/O contract:
    input  = GraphInput   (the locked `Request` fields + `case_id`/`jurisdiction`)
    output = ScopedRequest (the bare locked contract; the optional narrowing
             `ClarificationDraft` now rides EMBEDDED on `ScopedRequest.clarification`)

Output note (per the approved gate decision): `ScopedRequest` now carries an
optional `clarification: ClarificationDraft | None` field, so the graph emits a
bare `ScopedRequest` — there is no longer an `IntakeResult` envelope. On the
vague path the narrowing rides on `ScopedRequest.clarification` (and `is_vague`
is True); on the clean path `clarification` is None. The original interpreted
scope always stays in `subject`/`record_types`/`extracted_fields` — the narrowing
is only ever a suggestion inside the draft, never a silent rewrite of the scope
(§3). The canonical clarification round lives ONLY on
`ScopedRequest.clarification_round`; `ClarificationDraft` no longer carries a round.

§8.2/§8.3 failure posture: there is no structurally-valid-but-fake fallback. The
agent parses the LLM output into `ScopedRequest` at the boundary (§8.3); on the
FIRST validation failure it re-prompts once with the specific violation, and if
the second attempt still fails — or a permanent/auth precondition is missing
(e.g. no `ANTHROPIC_API_KEY`, §8.2 "permanent → no retry") — it RAISES
`IntakeUnrecoverableError`. Because the agent runs as a Maestro Service Task, the
raised failure makes Maestro route the case to the human dead-letter/close-out
queue with full case state preserved (§8.2). The human flag now lives there, not
in the agent's data output.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from shared.contracts import (
    FEDERAL_FOIA,
    ClarificationDraft,
    ScopedRequest,
)

from config import model_for, temperature_for


# ─────────────────────────────────────────────────────────────────────────────
# Unrecoverable-failure signal (§8.2)
# ─────────────────────────────────────────────────────────────────────────────


class IntakeUnrecoverableError(RuntimeError):
    """Raised when the Intake/Scoping step cannot produce a valid ScopedRequest.

    Per §8.2 this is the *unrecoverable* path: a permanent precondition is
    missing (e.g. `ANTHROPIC_API_KEY` not set — "permanent → no retry"), or the
    LLM output is still invalid after the one §8.3 re-prompt. The agent runs as a
    Maestro Service Task, so raising (rather than emitting a fake but
    structurally-valid `ScopedRequest`) is what makes Maestro pause the case and
    route it to the human dead-letter/close-out queue with full state preserved.
    The message carries the `request_id` and the specific violation for that queue.
    """

    def __init__(self, request_id: str, reason: str) -> None:
        self.request_id = request_id
        self.reason = reason
        super().__init__(f"Intake unrecoverable for request '{request_id}': {reason}")


# ─────────────────────────────────────────────────────────────────────────────
# Graph input model
# ─────────────────────────────────────────────────────────────────────────────


class GraphInput(BaseModel):
    """Inbound payload for the scoping agent.

    The locked `Request` is pre-identity (the requester has no case id yet);
    Maestro opens the case and supplies `case_id`/`jurisdiction` when it invokes
    this Service Task, so they arrive here alongside the raw request fields and
    are threaded onto the `ScopedRequest` envelope. Defaults keep the agent
    independently runnable from a bare `Request` fixture.
    """

    request_id: str = Field(description="Portal-assigned id for the submission.")
    requester: str = Field(description="Requester identity (stubbed in MVP).")
    text: str = Field(description="Free-text request as submitted.")
    submitted_at: datetime = Field(description="Submission timestamp (Clock seam).")
    attachments: list[str] = Field(default_factory=list)
    case_id: str = Field(
        default="intake-unassigned",
        description="Maestro case id; supplied by the case model on invocation.",
    )
    jurisdiction: str = Field(
        default=FEDERAL_FOIA,
        description="Legal regime; only 'federal_foia' in MVP, passed as a real param.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal LLM structured-output schema (NOT a shared contract)
# ─────────────────────────────────────────────────────────────────────────────


class _ScopingDraft(BaseModel):
    """Constrained shape the scope LLM must return.

    Deliberately narrower than `ScopedRequest`: it carries only what the model
    decides (interpretation + ambiguity), never the identity envelope fields
    (`case_id`/`jurisdiction`) — those are threaded from the request context, not
    invented by the model. This separation is what guarantees the agent cannot
    fabricate a case id and cannot silently drop the original scope.
    """

    subject: str = Field(description="Normalized subject of the request (faithful to the ORIGINAL ask).")
    track: Literal["fast_track", "standard", "complex"] = Field(
        description="Triage track: fast_track = narrow & specific; complex = broad/multi-department/sensitive."
    )
    record_types: list[str] = Field(
        default_factory=list, description="Record types in scope (e.g. ['email','memo']); [] if unclear."
    )
    departments_hint: list[str] = Field(
        default_factory=list, description="Departments the requester explicitly named; [] otherwise."
    )
    extracted_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Normalized fields: any of date_from/date_to/parties/subject_detail the text supports.",
    )
    is_vague: bool = Field(
        description="True iff the scope is too vague to search effectively (missing time bound, subject, or custodian)."
    )
    vagueness_reason: Optional[str] = Field(
        default=None, description="If is_vague: what makes it un-searchable (used to draft the clarification)."
    )
    suggested_narrowing: Optional[str] = Field(
        default=None,
        description="If is_vague: a NARROWER scope offered as optional. Never replaces the original scope.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graph state
# ─────────────────────────────────────────────────────────────────────────────


class State(BaseModel):
    """Internal state flowing between nodes."""

    # Echoed inbound fields (so nodes don't re-derive from GraphInput).
    request_id: str
    requester: str
    text: str
    submitted_at: datetime
    attachments: list[str] = Field(default_factory=list)
    case_id: str = "intake-unassigned"
    jurisdiction: str = FEDERAL_FOIA

    # Produced by the scope node.
    draft: Optional[_ScopingDraft] = None


# ─────────────────────────────────────────────────────────────────────────────
# Prompting
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the Intake/Scoping officer for a U.S. federal FOIA (Freedom of \
Information Act) request-processing system. FOIA runs on a presumption of openness: your job is \
to interpret the request so it can be searched and disclosed, NOT to find reasons to deny it.

For the request you are given, decide:
1. subject — a normalized, faithful restatement of WHAT records the requester is asking for. \
Stay true to the original ask; do not narrow it.
2. track — triage difficulty:
   - "fast_track": narrow, specific, single clear subject and likely a single custodian.
   - "standard": a normal request with a clear subject but some breadth.
   - "complex": broad, multi-department, sensitive, or likely to involve many records/exemptions.
3. record_types — the kinds of records implied (e.g. email, memo, report, contract, spreadsheet). \
Empty list if genuinely unclear.
4. departments_hint — only departments the requester EXPLICITLY named. Empty otherwise; the search \
stage decides custodians.
5. extracted_fields — normalize any of: date_from, date_to, parties, subject_detail that the text supports.
6. is_vague — TRUE only if the scope is too vague to run a meaningful search: e.g. no subject, no time \
bound on an open-ended ask, or no way to pick a custodian. A specific, time-bounded, single-subject \
request is NOT vague.
7. If is_vague: vagueness_reason (what makes it un-searchable) and suggested_narrowing (a NARROWER \
scope you could fill faster). The narrowing is OPTIONAL for the requester — never a replacement.

Return ONLY the structured object. Be conservative about marking something vague: only do so when a \
search genuinely cannot proceed."""


def _human_prompt(state: State) -> str:
    return (
        f"Request id: {state.request_id}\n"
        f"Submitted at: {state.submitted_at.isoformat()}\n"
        f"Attachments: {state.attachments or 'none'}\n\n"
        f"Request text:\n\"\"\"\n{state.text}\n\"\"\""
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
         (robot-scoped) is tried first, then ``retrieve`` (direct value).

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
            # `retrieve_secret` returns the decrypted string; `retrieve` returns an
            # Asset whose `.value` carries the string.
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
    introspection) never requires the SDK/key to be present. Model is resolved
    via `model_for(step)` — never hardcoded. The API key is resolved by the
    caller (env → Orchestrator asset) and passed in explicitly so the robot path
    works even when ``ANTHROPIC_API_KEY`` is not in the process environment. If
    the configured model id is unavailable the Anthropic client surfaces it at
    call time; the caller's fallback then applies.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model_for(step),
        temperature=temperature_for(step),
        max_tokens=1024,
        timeout=60,
        api_key=api_key,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Nodes (async per the required-structure conventions)
# ─────────────────────────────────────────────────────────────────────────────


async def scope_node(state: State) -> State:
    """Interpret the request into a `_ScopingDraft` (Sonnet per §9).

    §8.3 posture: the model returns the constrained `_ScopingDraft`; on a parse/
    validation failure we RE-PROMPT ONCE with the specific violation. If the
    second attempt still fails we RAISE `IntakeUnrecoverableError` (§8.2
    unrecoverable) rather than emit an invalid or faked result — Maestro then
    routes the case to the human dead-letter queue with full state. Structured
    output is requested via the model's native schema binding; a JSON-text
    fallback parser covers providers/paths that don't honour the binding.
    """
    api_key = _resolve_anthropic_key()
    if not api_key:
        # No key from env (.env / bound %ASSETS% env ref) AND none from the
        # Orchestrator asset fallback: a PERMANENT precondition failure (§8.2
        # "permanent → no retry"). Nothing to retry, so raise. Maestro routes the
        # case to the human dead-letter queue with full state preserved.
        raise IntakeUnrecoverableError(
            state.request_id,
            "ANTHROPIC_API_KEY unavailable (not in env and Orchestrator asset "
            f"'{ANTHROPIC_KEY_ASSET}' not readable); scoping LLM step could not "
            "run (permanent, §8.2).",
        )

    llm = _build_llm("scope", api_key)
    structured = llm.with_structured_output(_ScopingDraft)
    base_messages = [SystemMessage(_SYSTEM_PROMPT), HumanMessage(_human_prompt(state))]

    last_error: Optional[str] = None
    for attempt in range(2):  # one initial try + one re-prompt (§8.3)
        messages = list(base_messages)
        if attempt == 1 and last_error is not None:
            messages.append(
                HumanMessage(
                    "Your previous response did not satisfy the required schema: "
                    f"{last_error}. Return ONLY a valid object with all required fields."
                )
            )
        try:
            draft = await structured.ainvoke(messages)
            if isinstance(draft, _ScopingDraft):
                return state.model_copy(update={"draft": draft})
            # Some bindings return a dict — validate it.
            draft = _ScopingDraft.model_validate(draft)
            return state.model_copy(update={"draft": draft})
        except ValidationError as exc:
            last_error = "; ".join(f"{'/'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        except Exception as exc:  # transport/parse error: try the raw-JSON fallback once
            last_error = str(exc)
            parsed = await _raw_json_fallback(llm, base_messages)
            if parsed is not None:
                return state.model_copy(update={"draft": parsed})

    raise IntakeUnrecoverableError(
        state.request_id,
        f"scoping LLM could not produce a valid interpretation after one §8.3 re-prompt: {last_error}",
    )


async def _raw_json_fallback(llm, base_messages) -> Optional[_ScopingDraft]:
    """Ask for raw JSON and parse it into `_ScopingDraft`; None if it still fails.

    Reached only from the broad `except` in `scope_node` — i.e. a transport/parse
    error from the native structured-output binding, not a schema `ValidationError`.
    It therefore re-sends the original prompt with an explicit JSON-shape
    instruction rather than `last_error`: there is usually no schema violation to
    echo here, the binding itself failed. (Schema-violation feedback is carried by
    the `attempt == 1` structured re-prompt in `scope_node`.)
    """
    try:
        instruction = HumanMessage(
            "Respond with ONLY a JSON object with keys: subject (str), track "
            "('fast_track'|'standard'|'complex'), record_types (list[str]), departments_hint "
            "(list[str]), extracted_fields (object of str→str), is_vague (bool), "
            "vagueness_reason (str|null), suggested_narrowing (str|null)."
        )
        resp = await llm.ainvoke(base_messages + [instruction])
        content = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return None
        return _ScopingDraft.model_validate_json(content[start : end + 1])
    except Exception:
        return None


async def assemble_node(state: State) -> ScopedRequest:
    """Assemble + boundary-validate the bare typed `ScopedRequest` (§8.3 posture).

    Builds the `ScopedRequest` from the model's draft PLUS the identity envelope
    threaded from the request context. The ORIGINAL interpreted scope is preserved
    verbatim in `subject`/`record_types`/`extracted_fields`; on the VAGUE path the
    optional narrowing rides on the embedded `ScopedRequest.clarification`
    (`is_vague=True`), on the CLEAN path `clarification` is None (`is_vague=False`).
    The canonical clarification round lives ONLY on
    `ScopedRequest.clarification_round`; the embedded `ClarificationDraft` no
    longer carries a round.

    §8.3 boundary validation: constructing `ScopedRequest`/`ClarificationDraft`
    runs their locked Pydantic contracts. A malformed value cannot leave this
    node — on a construction (validation) failure we RAISE
    `IntakeUnrecoverableError` so Maestro routes to the human dead-letter queue
    (§8.2); we never emit a faked-but-valid placeholder.
    """
    if state.draft is None:  # defensive: scope_node either set draft or already raised
        raise IntakeUnrecoverableError(state.request_id, "no interpretation was produced by the scope step.")

    draft = state.draft

    clarification: Optional[ClarificationDraft] = None
    clarification_round = 0
    if draft.is_vague:
        reason = (draft.vagueness_reason or "the request is too broad to search effectively").strip()
        narrowing = (draft.suggested_narrowing or "").strip() or None
        if narrowing:
            message = (
                f"To process your request faster, we could focus on a narrower version: {narrowing}. "
                "I can fill this narrower version faster — or keep your original request as submitted. "
                "Just let us know which you prefer."
            )
        else:
            message = (
                f"Your request as written is hard for us to search because {reason}. "
                "Could you share a few more details (a time period, a specific subject, or the office "
                "involved)? You can also ask us to proceed with your original request as submitted."
            )
        try:
            clarification = ClarificationDraft(
                message=message,
                suggested_narrowing=narrowing,
            )
        except ValidationError as exc:
            raise IntakeUnrecoverableError(
                state.request_id, f"ClarificationDraft failed boundary validation: {exc.errors()}"
            ) from exc
        # First clarification; sending tolls the clock (§5). The round is canonical
        # on ScopedRequest.clarification_round and feeds the §8.5 idempotency key.
        clarification_round = 1

    try:
        return ScopedRequest(
            case_id=state.case_id,
            jurisdiction=state.jurisdiction,
            request_id=state.request_id,
            track=draft.track,
            subject=draft.subject,  # ORIGINAL interpreted scope, never the narrowing
            extracted_fields=dict(draft.extracted_fields),
            record_types=list(draft.record_types),
            departments_hint=list(draft.departments_hint),
            is_vague=draft.is_vague,
            clarification_round=clarification_round,
            clarification=clarification,
        )
    except ValidationError as exc:
        raise IntakeUnrecoverableError(
            state.request_id, f"ScopedRequest failed boundary validation: {exc.errors()}"
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Graph wiring
# ─────────────────────────────────────────────────────────────────────────────

builder = StateGraph(State, input=GraphInput, output=ScopedRequest)
builder.add_node("scope", scope_node)
builder.add_node("assemble", assemble_node)
builder.add_edge(START, "scope")
builder.add_edge("scope", "assemble")
builder.add_edge("assemble", END)

graph = builder.compile()
