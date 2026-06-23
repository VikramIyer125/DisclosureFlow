# Review & Redaction Agent — DisclosureFlow stage 4 (THE HERO)

Derived from design-brief §3 (Review & Redaction), §2 stage 4 (Review & redaction
proposal), §5 (HITL — redaction-approval gate), §7 (PolicyProvider seam), §8.1
(confidence routing), §8.2 (step failure policy), §8.3 (typed-output validation),
§9 (models per step), §10 (data contracts). This file is kept in sync with what
is actually built. For agent-template/SDK/CLI patterns see `.agent/`
(auto-generated). For the build entry point see the repo-root `Makefile`.

> Note: regenerate the schema with `make init AGENT=review-redaction-agent`, which
> runs `uipath init --no-agents-md-override` so this authored file and `.agent/*`
> are NOT clobbered by the CLI's doc generator.

## What this agent is

ONE single-purpose LangGraph coded agent that performs exactly one stage's
reasoning: given the `CandidateRecord`s the search stage surfaced, it (1) decides
each record's RESPONSIVENESS and (2) for each responsive record proposes zero or
more redactions, each grounded in a specific PolicyProvider rule with a
source-grounded foreseeable-harm rationale and a filled, data-driven exemption
test. It returns a typed list of `RedactionProposal`s, each carrying a DERIVED
confidence signal (§8.1), §8.3-validated at the boundary.

### Disclosure posture (the legally load-bearing part)

**The default posture is DISCLOSURE.** FOIA runs on a presumption of openness and
a foreseeable-harm standard. This agent:
- **never withholds by default** — zero proposals on a record is the common,
  correct outcome (a fully disclosable record);
- **never withholds on its own authority** — it *proposes*, then a human records
  officer approves/rejects/edits every proposed withholding at the §5 gate
  (a LangGraph interrupt INSIDE this agent, `approval_gate`). ONLY after the human
  decides does the agent emit `ApprovedRedaction[]`. A REJECTED over-redaction
  RELEASES its content (the disclosure default holds THROUGH the gate).
- **must justify every withholding** against a specific PolicyProvider `rule_id`,
  with a source-grounded foreseeable-harm `rationale` and a complete legal test.
  The burden is on withholding, mirroring FOIA's presumption of openness.

It is **not** a supervisor. It never calls the other two agents and contains no
cross-stage sequencing or routing. The reject→revise loop, the high-confidence
batch-vs-full-review routing, and the dead-letter routing all live in the Maestro
Case model, not in this graph (CLAUDE.md hard rule 1a).

## Graph shape (stage-4 reasoning + the §5 human gate)

```
START → review → assemble → approval_gate → END
                              └─(reject→revise)→ re-run targeted review → re-interrupt (bounded)
```

The agent performs stage-4 reasoning (`review` + `assemble`) AND the §5
redaction-approval human gate (`approval_gate`), which the brief explicitly places
**inside this agent as a LangGraph interrupt** so the officer's feedback re-enters
this graph to drive the revise loop. This is still ONE single-purpose graph: it
never calls the other agents and adds no cross-stage routing. The interrupt is the
only new control construct.

- **review** (LLM, Opus 4.8 per §9): for each record, decides `is_responsive`
  and proposes zero-or-more redactions into a constrained internal `_ReviewDraft`
  (`reviews: [{record_ref, is_responsive, redactions:[{rule_id, quote, rationale,
  test_elements}]}]`). The PolicyProvider seam is consulted to BUILD THE PROMPT —
  each record is shown ONLY the rules `get_applicable_rules(jurisdiction,
  record_type)` returns, so the model can only choose among allowed rules. §8.3
  posture: a boundary gate (`_draft_validation_error`) converts each proposed
  redaction into a real `RedactionProposal` and runs the SHARED
  `validate_proposal` (rule_id ∈ allowed set, required test elements populated,
  record_ref in scope) PLUS a span/quote-grounding check. On the FIRST violation
  it RE-PROMPTS ONCE with the specific violation; if the second attempt still
  fails it RAISES `ReviewUnrecoverableError` (§8.2) rather than emit an invalid or
  faked proposal.
- **assemble**: rebuilds each locked `RedactionProposal`, stamps the pack
  (`pack_id`/`pack_version` from `pack_metadata` — never the model), DERIVES
  confidence via `derive_confidence(rule, test_result)` (§8.1 — never a model
  field), and runs the FINAL §8.3 `validate_proposal`. Any surviving violation
  RAISES `ReviewUnrecoverableError`. Writes the validated `proposals` + the
  responsiveness echo into `State` for the gate (shared assembly is factored into
  `_assemble_proposals`, used by both this node and the revise loop).
- **approval_gate** (the §5 HUMAN GATE — legally load-bearing): surfaces the
  proposals to a records officer via `interrupt(CreateEscalation(...))` (an Action
  Center task), PAUSES, and on resume applies the officer's per-proposal decision:
  - **accept** → emit `ApprovedRedaction(decision="approved")`.
  - **edit** → re-locate the officer's `edited_quote` in the record (same
    quote-grounding discipline as the model; never trust officer offsets) → emit
    `ApprovedRedaction(decision="edited", edited_span=...)`.
  - **reject + `revise=false`** → the over-redaction is WRONG: the content is
    RELEASED (disclosure default). Recorded as `ApprovedRedaction(decision="rejected")`
    with a no-approval sentinel token (the §8.4 guard blocks any `rejected`
    redaction outright, so it can never leak); surfaced for the corrections log
    (advisory, §7).
  - **reject + `revise=true`** → queue for the §5.C reject→revise loop.
  - **no decision** for a proposal → SAFE DEFAULT = released/`rejected` (the burden
    is on withholding — silence never approves).
  After applying decisions, any revise-queued proposals are re-reasoned
  (`_revise_proposals` re-runs the targeted `review` reasoning with the officer's
  note, then `_assemble_proposals` re-validates §8.3) and **re-interrupted** for
  re-review — bounded by `max_revise_rounds()` (config, default 1). When the bound
  is reached, unresolved revise items fall back to the disclosure default
  (recorded `rejected`, NOT withheld). Returns the final `ReviewResult`.

  Clean case (zero proposals): the gate skips the interrupt entirely and returns an
  empty `approved` set — nothing to withhold IS the disclosure default. (Maestro
  still routes the package to the §3c final-release User Task downstream.)

## I/O contract

- **Input** = `GraphInput`: a list of locked `CandidateRecord`s (`records`) plus
  `case_id` / `jurisdiction` (the top-level identity threaded onto every emitted
  proposal) and `officer` (default `"records.officer"` — the Action Center
  assignee + the identity stamped on each `ApprovedRedaction.officer`; the
  authoritative `approval_token` still comes from the completed task, not this
  field). `case_id` and `records` are required; `jurisdiction` / `officer` default.
  The agent does NO RecordStore I/O — Maestro hands it the records after the
  fan-out, so it stays a pure reasoning unit (independently runnable from a
  fixture). Each `CandidateRecord.text` is what the model reads to find exemptions;
  a record with no text is marked non-responsive (it cannot be assessed).
- **Resume payload** (the §5 gate's human input) = `ApprovalResume{decisions:
  [OfficerDecision]}`, where `OfficerDecision = {proposal_id, decision ∈
  accept|reject|edit, edited_quote?, revise: bool, note?}`. Delivered as the
  completed Action Center task (platform) or the `uipath run --resume` JSON
  (local); both are normalized into `ApprovalResume`. These are AGENT-LOCAL
  schemas shaping the human input — NOT pipeline contracts.
- **Output** = `ReviewResult`, a **thin agent-local envelope**:
  - `approved: list[ApprovedRedaction]` — the §10 stage-5 officer decisions after
    the §5 gate. Only `approved`/`edited` entries carry release-bound bytes
    (`approval_token` + `approved_content_hash`, §8.4); `rejected` entries record
    a released-content decision for audit/corrections. **This is now the agent's
    authoritative output** — a human has decided.
  - `proposals: list[RedactionProposal]` — every proposal originally proposed
    (each §8.3-validated, confidence DERIVED), kept for the audit trail (what was
    proposed vs. what was approved).
  - `reviewed: list[RecordReview]` — the per-record responsiveness decisions
    (`record_ref`, `is_responsive`, `proposal_count`) so Maestro can mark
    non-responsive records and distinguish a responsive record with ZERO
    proposals (clean, fully disclosable) from a non-responsive one.

  `ReviewResult` **composes** the locked, shared `RedactionProposal` and
  `ApprovedRedaction` — it does not redefine them. It is deliberately **not** a new
  shared pipeline contract (that would be the IntakeResult mistake); mirrors the
  Custodian agent's `SearchPlan`. See ASSUMPTIONS.md.

### §5 redaction-approval gate — interrupt API, approval_token, content hash (§8.4)

- **Interrupt primitive (installed SDK):** `interrupt(CreateEscalation(...))` from
  `uipath.platform.common` — confirmed against the *installed* `uipath` 2.11.x
  split packages (`uipath_core`/`uipath_runtime`/`uipath_langchain`). The older
  `interrupt(CreateAction(...))` documented in Context7 / the case-model-spec is
  the pre-split 2.x monolithic API and is **not importable in this version**; the
  install uses `CreateEscalation` (the same `CreateTask` shape: `app_name`,
  `app_folder_path`, `title`, `data`, `assignee`). See ASSUMPTIONS.md. The import
  is lazy inside the node so `uipath init` / non-platform contexts don't require it.
- **Action Center task payload** (`CreateEscalation.data`) carries, per proposal:
  `record_ref`, `span` (start/end/quote), `rule_id`, `citation`, `rationale`,
  `test_result`, and the DERIVED `confidence` (+ `full_individual_review_required`
  = `confidence.level == "low"`). Per §5 this lets the app present b6/b7c
  (always `low`/balancing) for **full individual review** and group high-confidence
  non-balancing (e.g. b5 high) for lighter approval. Plus a deterministic
  `idempotency_key = "<case_id>:redaction_review:round<N>"` (§8.5).
- **`approval_token` derivation** (tied to the SPECIFIC human approval, never
  invented): from the completed Action Center task's durable identity on resume —
  its `key` (GUID) or `id` — as `"actiontask:<task_key>:<proposal_id>"` (per-proposal
  so two proposals in one task get distinct, guard-matchable tokens). LOCAL/test
  fallback (a plain `--resume` JSON with no task identity):
  `"local-approval:<sha256(proposal_id)>"` — still a real, reproducible binding to
  the specific approved proposal (not random), so a replay yields the same token
  (idempotent). A `rejected` decision gets the sentinel `"rejected-no-approval"`.
- **`approved_content_hash` (§8.4)** = `sha256` of the **record-level**
  post-redaction bytes: ALL of that record's `approved`/`edited` spans applied with
  the shared `█` length-preserving mask (`shared/release/mask.py`), UTF-8 encoded.
  This is computed via the SAME `shared.release.mask.redacted_content_hash` the
  release step uses, so the hash is **byte-identical** to what
  `steps/release_step.py` recomputes and the §8.4 release guard re-checks — cross-
  verified locally (the agent's approvals feed the real release step and the guard
  ALLOWS the release). Every `ApprovedRedaction` on a record carries the same
  record-level hash (matching the release step's per-record expectation).
- **Idempotency (§8.5):** `interrupt()` is memoized by LangGraph checkpointing, so
  a pause/resume/replay does NOT recreate the task or double-emit. Task `data` and
  `proposal_id` are keyed deterministically by `case_id` + `record_ref` + span +
  `round`, so a replay computes identical keys.
- **Revise bound:** `MAX_REVISE_ROUNDS` (config, `config.max_revise_rounds()`,
  default 1). After the bound, unresolved revise items resolve to the disclosure
  default (released, recorded `rejected`) — never an infinite human/agent loop,
  never a silent withholding.

Each emitted `RedactionProposal` carries: identity (`case_id`/`jurisdiction`), the
pack stamp (`pack_id`/`pack_version` from the seam), `record_ref` (in the input
set), `span` (char range over the record text + the verbatim `quote`), `rule_id`
(∈ the PolicyProvider's returned set for that record's record_type), `citation`
(copied from the Rule — never the model), the foreseeable-harm `rationale`, the
generic `test_result` (test copied from the Rule; `elements` filled per the Rule's
`required_test_elements`), and the DERIVED `confidence`.

### Span/quote grounding (LIVE-driven design)

The model supplies the verbatim `quote` to redact, **not** character offsets — a
live run proved Opus reliably picks the right substring but mis-counts character
offsets. The agent LOCATES the quote in the record text (`_locate_span`) and
DERIVES the span, so `record.text[start:end] == quote` holds **by construction**.
Grounding stays strict: the quote must appear in the record text **exactly once**
(the prompt tells the model to add surrounding context to disambiguate a repeated
phrase); absent/ambiguous/empty quote → a grounding failure that re-prompts then
raises. This keeps the redaction provably tied to real source text without
trusting model-computed integers.

## Confidence is DERIVED, never asked (§8.1)

The model fills the legal test; a deterministic step computes confidence via the
shared `derive_confidence(rule, test_result, self_consistency=None)`. The agent
MUST call it and attach its result; it NEVER sets `RedactionProposal.confidence`
from a model field (the internal `_ReviewDraft` has no confidence field, so the
model *cannot*). Priority order, first match wins:

1. **(a) balancing-test rule (b6, b7c) ⇒ ALWAYS `low` / full human review**,
   regardless of any other signal (`derivation="balancing_always_full_review"`).
   **Proven live**: in Journey C, b6 and b7c proposals come out `low` even with a
   complete, unhedged test, while the b5 (deliberative, foreseeable_harm test)
   proposal with a complete test comes out `high`.
2. **(b) any required test element hedged/blank ⇒ `low`**
   (`derivation="incomplete_test_elements"`).
3. **(c) self-consistency disagreement** — `self_consistency` is `None` here
   (Milestone-5 stretch; this is the only agent that will run it). Seam left in
   `derive_confidence`'s third param.
- otherwise ⇒ `high` (`derivation=None`).

## §8.3 typed-output validation (at the boundary)

The instant the model returns, every proposal is validated with the SHARED
validators in `shared/contracts/validation.py` (`validate_proposal` /
`validate_test_completeness`) — not a re-implementation:
1. `rule_id` ∈ the set `get_applicable_rules` returned for THAT record's
   record_type (the closed set is one source of truth: it is BOTH what the model
   is shown AND what the validator checks against, so prompt and validation can
   never diverge);
2. the rule's `required_test_elements` are all populated (not blank, not hedged) —
   data-driven from the pack, never hardcoded per exemption;
3. `record_ref` is in the input candidate set.
On the FIRST violation: re-prompt ONCE with the specific violation. Still failing
⇒ RAISE. An ungrounded/over-broad withholding is therefore never silently
emitted — it escalates.

## §8.2 failure posture (raise, don't fake)

There is no structurally-valid-but-fake fallback. On an UNRECOVERABLE condition
the agent RAISES a module-level `ReviewUnrecoverableError(RuntimeError)` carrying
`case_id`, the specific `reason`, and (when applicable) the `record_ref`. Because
the agent runs as a Maestro Service Task, raising makes Maestro pause the case and
route it to the human dead-letter queue with full state preserved (§8.2) — it does
**not** emit a fake proposal.

Unrecoverable conditions (raise):
- No input `records` — a **permanent** precondition (a search that found no
  records is a legitimate-negative the case model handles upstream; this Service
  Task should not be invoked with an empty set).
- Missing `ANTHROPIC_API_KEY` — a **permanent** precondition (§8.2 "permanent →
  no retry").
- LLM output still §8.3-invalid after the one re-prompt (off-allowed-set rule_id,
  blank/hedged required element, out-of-scope record_ref, or an ungrounded/
  ambiguous span quote).
- A constructed `RedactionProposal` failing boundary validation, or a proposal
  surviving to `assemble` that still fails the final §8.3 check.

Legitimate negatives (flow forward, NOT a failure):
- **"No exemption applies"** → the record is responsive with ZERO proposals (the
  disclosure default).
- **Non-responsive record** → `is_responsive=False`, zero proposals (a
  wrong_docs/off-topic record).

## Import surface from `shared`

```python
from shared.contracts import (
    FEDERAL_FOIA, CandidateRecord, RedactionProposal, ExemptionTestResult,
    Rule, Span, TestElement, ConfidenceSignal,
    derive_confidence, validate_proposal,
)
from shared.seams import FederalFoiaPackProvider, PolicyProvider
```

The agent does **not** redefine any contract. `_ReviewDraft` / `_RecordReviewDraft`
/ `_RedactionDraft` / `_TestElementDraft` are internal LLM-output schemas (not
shared contracts): deliberately narrower than the locked contracts, carrying NO
identity, NO citation, NO pack stamp, and NO confidence — so the model can never
fabricate a `case_id`, invent a citation, or set its own confidence. `ReviewResult`
/ `RecordReview` are the thin agent-local OUTPUT envelope (see above).

## PolicyProvider seam (the FIRST seam consumer) + policy-pack on the robot

This is the FIRST agent to use a seam and the policy-pack data. The
`FederalFoiaPackProvider` (demo backing) loads `policy-packs/federal-foia/pack.json`,
which is OUTSIDE `shared/` and is a **.json** (not a .py). Two things make it reach
the robot:

1. **Vendoring.** The repo-root `Makefile`'s `vendor` target rsyncs BOTH `shared/`
   AND `policy-packs/` into this agent dir (the agent is listed in the Makefile's
   `PACK_DATA_AGENTS`). The vendored `policy-packs/` is gitignored here (build
   artifact; canonical source is repo-root `policy-packs/`).
2. **`uipath pack` bundling.** `uipath.json` sets
   `packOptions.fileExtensionsIncluded: [".json"]` so the `.json` is not dropped
   from the nupkg (`uipath pack` includes `.py` by default but drops other
   extensions unless told — see the platform note in ASSUMPTIONS.md).

`main._resolve_pack_dir()` resolves the pack relative to THIS file: it prefers the
vendored copy bundled alongside `main.py` (the robot path) and falls back to the
canonical repo-root copy (local dev), overridable via `POLICY_PACK_DIR`.

The agent reasons over WHATEVER `get_applicable_rules` returns — it never
hardcodes rule ids, citations, or counts (brief §7). Adding a new exemption is a
pack edit, not a code change. `get_rule` is available for citation lookup;
`pack_metadata` provides the `PackStamp` (`pack_id`/`version`).

## §5 gate BUILT — remaining additive seams (do NOT build now)

The §5 **redaction-approval gate is a LangGraph INTERRUPT inside THIS agent**
(`approval_gate`) and is **now built** (see "Graph shape" / the §5 I/O section).
Two additive enhancements still attach AFTER this gate / inside `assemble` without
reshaping it:
- **Corrections-memory lookup** (advisory, §7): before/within the gate, retrieve
  past officer corrections for the record context and surface them into the Action
  Center task payload (advisory only — never sets `rule_id`/confidence, never
  bypasses the human gate). The reject/edit decisions this gate already records are
  the corrections-log writes' source.
- **Self-consistency sampling** (§8.1c, stretch): re-run the `review` step 3–5× and
  feed the disagreement into `derive_confidence(..., self_consistency=...)` (the
  third param is already wired; `assemble` passes `None` today). This is the only
  agent that runs it.
Both are additive (new lookups/passes), not a reshape of `review`/`assemble`/
`approval_gate`.

## Hard rules honoured

- **Disclosure default.** Never withholds by default; zero proposals is the
  common correct answer. Never withholds on its own authority — a human approves
  downstream.
- **Every redaction grounds in a REAL PolicyProvider rule (§8.3).** rule_id ∈ the
  returned set; citation copied from the seam; required test elements data-driven
  from the pack. No exemption from model memory.
- **Confidence DERIVED (§8.1), never asked.** Balancing (b6/b7c) always full human
  review; the model has no confidence field.
- **One graph, one stage, no agent-to-agent calls, no Maestro logic in Python.**
- **No RecordStore I/O** — records are injected; the agent reasons over what it is
  given.
- **`jurisdiction` passed from day one**, threaded onto every proposal.
- **Fail by raising, not faking (§8.2).**

## Model-per-step config (§9, §13)

Model is a per-step config value, never hardcoded in business logic. `config.py`
exposes `model_for(step)`:

- resolution order: `<STEP>_MODEL` env var → the §9 default for that step →
  `DEFAULT_MODEL` (`claude-opus-4-8`).
- the one step is `review` → `claude-opus-4-8` (env `REVIEW_MODEL`). The
  Milestone-5 self-consistency sampling re-runs this SAME `review` step 3-5×, so
  it shares the key.

LLM calls go **directly to Anthropic** via `langchain_anthropic.ChatAnthropic`
(brief §13 — not UiPathChat). The API key is resolved by
`main._resolve_anthropic_key()`: `ANTHROPIC_API_KEY` env first, then the
Orchestrator secret-asset fallback (`DisclosureFlow_AnthropicApiKey`, the
confirmed-working serverless-robot key path). There is **no call-time model
fallback** — if the resolved model id is unavailable, the client raises at invoke
time, `review_node` runs ONE raw-JSON retry on the SAME model, then raises.

> **Opus 4.8 + temperature.** `claude-opus-4-8` REJECTS the `temperature`
> parameter (confirmed live on the Custodian agent, also Opus 4.8).
> `temperature_for` returns `None` by default and `_build_llm` OMITS `temperature`
> from the client; a temperature is sent only when `REVIEW_TEMPERATURE` is
> explicitly set (e.g. if a step is reconfigured onto a model that accepts it).

## Build / run (vendor-then-pack)

`shared/` AND `policy-packs/` are **vendored** into this dir at build time (build
artifacts, gitignored here; canonical sources are repo-root). Always build via the
repo-root `Makefile` so the vendoring rsync cannot be skipped:

```bash
make vendor  AGENT=review-redaction-agent   # copy shared/ + policy-packs/ in
make init    AGENT=review-redaction-agent   # vendor + uipath init (regenerate schema; keeps AGENTS.md)
make pack    AGENT=review-redaction-agent   # vendor + init + uipath pack (bundles the .json pack)
make publish AGENT=review-redaction-agent   # vendor + init + pack + uipath publish
```

A bare `uipath pack` without vendoring would silently omit `shared/` and the
policy pack and ImportError / FileNotFoundError on the robot. `pyproject.toml`
declares `pydantic` explicitly plus `langchain-anthropic` for the direct Claude
call.

## Local verification

```bash
make vendor AGENT=review-redaction-agent
cd agents/review-redaction-agent
# uipath run loads THIS dir's .env (empty); source the root .env for a live run:
set -a && . ../../.env && set +a
uv run uipath run agent --file fixtures/records_clean.json            # Journey A: responsive, 0 proposals
uv run uipath run agent --file fixtures/records_exemption_heavy.json  # Journey C: b5/b6/b7c proposals, DERIVED confidence
```

Fixtures double as demo review inputs:
- `fixtures/records_clean.json` (Journey A): one clean procurement email → marked
  responsive, ZERO proposals (the disclosure default, no withholding).
- `fixtures/records_exemption_heavy.json` (Journey C): three records — a personal-
  privacy HR email (b6), a pre-decisional deliberative memo (b5), and a
  law-enforcement IG report (b7c). Yields grounded proposals with the right rule
  ids, filled test results, pack-stamped, and DERIVED confidence: **b6/b7c come
  out `low`/full-review even with complete tests; b5 comes out `high`.**

Without `ANTHROPIC_API_KEY` (and with no readable Orchestrator asset) the run
RAISES `ReviewUnrecoverableError` (the §8.2 permanent-precondition path) rather
than returning a fake proposal — on UiPath this routes the case to the human
dead-letter queue.

### §5 gate — interrupt + resume (live vs. local)

A full `uipath run agent --file fixtures/records_exemption_heavy.json` runs the
live LLM (`review`→`assemble`) and then HITS the interrupt: the uipath runtime
tries to CREATE the real Action Center task by resolving the action app
`DisclosureFlow_RedactionReview` and pauses. That step needs (a) a valid
`UIPATH_ACCESS_TOKEN` and (b) the deployed review action app — **net-new platform
artifacts** authored separately (case-model-spec §10). Until they exist the live
run reaches `create_trigger` and reports a 401 on the app lookup (proving the
interrupt fires and reaches the real task-creation path).

The gate's pause/resume + decision/revise/emit LOGIC is verified locally without
the platform by driving `approval_gate_node` with a LangGraph `MemorySaver`
checkpointer and `Command(resume={"decisions":[...]})` — covering ACCEPT
(→ ApprovedRedaction with token + record-level hash), EDIT (officer `edited_quote`
re-located → `edited_span`), REJECT-as-release (→ content released, sentinel
token), the REJECT→revise round (re-interrupt with the revised proposal), and the
revise bound → disclosure-default fallback. The emitted approvals were fed into the
real `steps/release_step.py`: the §8.4 guard ALLOWS the release (token + hash
verify), confirming the agent's `approved_content_hash` matches the release step's
recompute. PENDING the real Action Center: the platform `--resume` round-trip with
a live task (run as part of the deploy round-trip).

The redaction-mask convention (`█`, length-preserving) is the §8.4 single source of
truth in `shared/release/mask.py`, imported by BOTH this agent (for the approval
hash) and `steps/release_step.py` (for re-application) — so the two can never
diverge and silently block every honest release.
