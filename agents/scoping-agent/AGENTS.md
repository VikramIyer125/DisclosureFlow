# Intake/Scoping Agent — DisclosureFlow stage 1-2

Derived from design-brief §3 (Intake/Scoping), §5 (clarification loop), §8.2
(step failure policy), §8.3 (typed-output validation), §9 (models per step), §10
(data contracts). This file is kept in sync with what is actually built. For
agent-template/SDK/CLI patterns see `.agent/` (auto-generated). For the build
entry point see the repo-root `Makefile`.

> Note: regenerate the schema with `make init AGENT=scoping-agent`, which runs
> `uipath init --no-agents-md-override` so this authored file and `.agent/*` are
> NOT clobbered by the CLI's doc generator.

## What this agent is

ONE single-purpose LangGraph coded agent that performs exactly one stage's
reasoning: it interprets a raw FOIA `Request`, detects ambiguity, proposes a
scope + triage `track`, and — when the scope is too vague to search — drafts a
requester-facing OPTIONAL narrowing suggestion (the §5 clarification loop). It
returns typed JSON validated at the agent boundary.

It is **not** a supervisor. It never calls the other two agents and contains no
cross-stage sequencing or routing. `is_vague` is a *signal* the Maestro Case
model reads to drive the clarification branch; the multi-turn re-scope loop, the
clock toll, and the close-out timer all live in Maestro, not in this graph
(CLAUDE.md hard rule 1a).

## Graph shape (single stage, linear)

```
START → scope → assemble → END
```

- **scope** (LLM, Sonnet 4.6 per §9): interprets the request into a constrained
  internal `_ScopingDraft` (subject, track, record_types, departments_hint,
  extracted_fields, is_vague + vagueness_reason + suggested_narrowing). Requests
  structured output via the model's native schema binding, with a raw-JSON
  fallback parser. §8.3 posture: on a parse/validation failure it RE-PROMPTS ONCE
  with the specific violation; if the second attempt still fails it RAISES
  `IntakeUnrecoverableError` (§8.2 unrecoverable) rather than emit an invalid or
  faked result.
- **assemble**: builds the bare `ScopedRequest` from the model's draft PLUS the
  identity envelope (`case_id`/`jurisdiction`) threaded from the request context,
  and — when vague — embeds a `ClarificationDraft` on
  `ScopedRequest.clarification`. Each constructed contract is validated by its own
  locked Pydantic model on construction; a malformed value cannot leave this node
  (a construction/validation failure raises `IntakeUnrecoverableError`).

## I/O contract

- **Input** = `GraphInput`: the locked `Request` fields (`request_id`,
  `requester`, `text`, `submitted_at`, `attachments`) plus `case_id` /
  `jurisdiction`, which Maestro supplies on invocation (the raw `Request` is
  pre-identity; the case id is assigned when the case opens). Defaults keep the
  agent independently runnable from a bare `Request` fixture.
- **Output** = a bare `ScopedRequest` (the locked contract). Per the approved
  gate decision there is **no `IntakeResult` envelope**:
  - `ScopedRequest` always carries the **original** interpreted scope in
    `subject` / `record_types` / `extracted_fields` / `departments_hint`.
  - `ScopedRequest.clarification: ClarificationDraft | None` — the narrowing
    SUGGESTION rides here, present **only** when `is_vague` is True (None on the
    clean path). It is the only place a narrowing appears, framed as optional.
  - `ScopedRequest.clarification_round` is the single **canonical** round (0 on
    the clean path, 1 on the first clarification). `ClarificationDraft` no longer
    carries a round — one source of truth for the §8.5 key.

There is no `flagged_for_human` / `flag_reason` field and no placeholder
ScopedRequest fallback. The human flag now lives where §8.2 puts it: on an
**unrecoverable** condition the agent RAISES `IntakeUnrecoverableError`
(a module-level `RuntimeError` subclass carrying `request_id` + the specific
violation). Because the agent runs as a Maestro Service Task, the raised failure
makes Maestro pause the case and route it to the human dead-letter/close-out
queue with full case state preserved — it does **not** emit a structurally-valid
but fake `ScopedRequest`.

Unrecoverable conditions (raise):
- Missing `ANTHROPIC_API_KEY` — a **permanent** precondition (§8.2 "permanent →
  no retry"); there is nothing to retry, so it raises immediately.
- LLM output still invalid after the one §8.3 re-prompt.
- A constructed `ScopedRequest` / `ClarificationDraft` failing boundary
  validation.

(Transient transport/parse errors inside `scope` are handled before this: one
raw-JSON fallback parse, then the §8.3 re-prompt. A "vague" interpretation is
NOT a failure — it flows forward as `is_vague=True` with an embedded
clarification, the §8.2 legitimate-negative path.)

## Import surface from `shared`

```python
from shared.contracts import (
    FEDERAL_FOIA, ClarificationDraft, Request, ScopedRequest,
)
```

The agent does **not** redefine any contract. `_ScopingDraft` is an internal
LLM-output schema (not a shared contract): it is deliberately narrower than
`ScopedRequest` and carries no identity fields, so the model can never fabricate a
`case_id` or silently drop the original scope.

## Hard rules honoured

- **Never silently narrows (§3).** The emitted `ScopedRequest.subject` /
  `record_types` / `extracted_fields` carry the original ask verbatim. Any
  narrowing lives only in `ScopedRequest.clarification.suggested_narrowing`, and
  the message text always offers "keep your original."
- **`jurisdiction` passed from day one**, threaded onto the `IdentityEnvelope`
  along with `case_id`.
- **One graph, one stage, no agent-to-agent calls, no Maestro logic in Python.**
- **Fail by raising, not faking (§8.2).** No structurally-valid-but-fake output;
  unrecoverable → raise → Maestro dead-letters to a human with state preserved.

## Model-per-step config (§9, §13)

Model is a per-step config value, never hardcoded in business logic. `config.py`
exposes `model_for(step)`:

- resolution order: `<STEP>_MODEL` env var → the §9 default for that step →
  `DEFAULT_MODEL` (`claude-sonnet-4-6`).
- steps + defaults: `scope`→`claude-sonnet-4-6` (env `INTAKE_MODEL`);
  `track`/`clarification`/`extract`→`claude-haiku-4-5-20251001` (envs
  `TRACK_MODEL` / `CLARIFICATION_MODEL` / `EXTRACT_MODEL`).

The thin build folds the light steps into the single `scope` node; the `track`/
`clarification`/`extract` entries exist so splitting them out to Haiku later is a
config edit, not a refactor. LLM calls go **directly to Anthropic** via
`langchain_anthropic.ChatAnthropic` (brief §13 — not UiPathChat). The API key is
read from `ANTHROPIC_API_KEY` (an Orchestrator secret asset on the robot). If a
configured model id is unavailable, fall back to `DEFAULT_MODEL` and note it in
`ASSUMPTIONS.md`.

## Build / run (vendor-then-pack)

`shared/` is **vendored** into this dir at build time (it is a build artifact,
gitignored here; the canonical source is repo-root `shared/`). Always build via
the repo-root `Makefile` so the vendoring rsync cannot be skipped:

```bash
make vendor  AGENT=scoping-agent   # copy shared/ in
make init    AGENT=scoping-agent   # vendor + uipath init (regenerate schema; keeps AGENTS.md)
make pack    AGENT=scoping-agent   # vendor + init + uipath pack
make publish AGENT=scoping-agent   # vendor + init + pack + uipath publish
```

A bare `uipath pack` without vendoring would silently omit `shared/` and
ImportError on the robot. `pyproject.toml` declares `pydantic` explicitly (the
robot installs only what is listed) plus `langchain-anthropic` for the direct
Claude call.

## Local verification

```bash
make vendor AGENT=scoping-agent
cd agents/scoping-agent
uv run uipath run agent --file fixtures/request_clean.json   # needs ANTHROPIC_API_KEY for the live scope
```

Fixtures double as demo intake inputs: `fixtures/request_clean.json` (Journey A,
specific → no clarification, fast_track → `ScopedRequest` with `is_vague=False`,
`clarification=None`) and `fixtures/request_vague.json` (Journey B, vague →
`is_vague=True` + embedded `ClarificationDraft`, original scope preserved).
Without `ANTHROPIC_API_KEY` the run RAISES `IntakeUnrecoverableError` (the §8.2
permanent-precondition path) rather than returning a fake result — on UiPath this
is what routes the case to the human dead-letter queue.
