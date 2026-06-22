# Custodian/Search Agent — DisclosureFlow stage 3

Derived from design-brief §3 (Custodian/Search), §2 stage 3 (Search & custodian
tasking), §7 (RecordStore seam), §8.2 (step failure policy), §8.3 (typed-output
validation), §8.5 (idempotency), §9 (models per step), §10 (data contracts).
This file is kept in sync with what is actually built. For agent-template/SDK/CLI
patterns see `.agent/` (auto-generated). For the build entry point see the
repo-root `Makefile`.

> Note: regenerate the schema with `make init AGENT=custodian-search-agent`,
> which runs `uipath init --no-agents-md-override` so this authored file and
> `.agent/*` are NOT clobbered by the CLI's doc generator.

## What this agent is

ONE single-purpose LangGraph coded agent that performs exactly one stage's
reasoning: given a `ScopedRequest` and the available department universe, it
(1) picks the SUBSET of departments to task and (2) generates per-department
`SearchTerms`. It returns a typed list of `SearchTask`s, each with a
DETERMINISTIC `task_id`.

It is a **pure reasoning unit**. It does **not**:
- run the record-store queries or execute the fan-out (that is the RecordStore
  seam / a downstream API Workflow, driven by Maestro);
- do any RecordStore I/O — the department universe is INJECTED as input
  (`available_departments`), sourced upstream by Maestro from
  `RecordStore.list_departments`;
- set per-department behavior/status (respond/slow/silent/wrong_docs) — that is
  the RecordStore demo backing's concern at query time; `SearchTask` has no
  status field;
- call the other two agents or contain any cross-stage sequencing/routing.

The fan-out across departments, the slow/silent/wrong-docs custodian
reminder→escalation branches, and the retry/dead-letter routing all live in the
Maestro Case model, not in this graph (CLAUDE.md hard rule 1a).

## Graph shape (single stage, linear)

```
START → plan → assemble → END
```

- **plan** (LLM, Opus 4.8 per §9): reasons over the scoped request + the injected
  `available_departments` and emits a constrained internal `_SearchPlanDraft`
  (`departments: [{department, keywords, record_types, date_from, date_to}]`).
  Requests structured output via the model's native schema binding, with a
  raw-JSON fallback parser. §8.3 posture: a SEMANTIC constraint gate
  (`_draft_constraint_error`) checks — beyond Pydantic shape — that at least one
  department is tasked, every chosen department is in the injected universe, no
  department is tasked twice, and each tasked department has ≥1 keyword. On a
  parse/validation/constraint failure it RE-PROMPTS ONCE with the specific
  violation; if the second attempt still fails it RAISES
  `CustodianUnrecoverableError` (§8.2 unrecoverable) rather than emit an invalid
  or faked plan.
- **assemble**: builds one `SearchTask` per chosen department from the model's
  draft PLUS the identity envelope (`case_id`/`jurisdiction`) threaded from the
  request context. The `task_id` is DERIVED deterministically from the department
  via `task_id_for` (never from the model). Each constructed `SearchTask` /
  `SearchTerms` is validated by its own locked Pydantic contract; a malformed
  value cannot leave this node (a construction/validation failure raises
  `CustodianUnrecoverableError`). Returns the `SearchPlan` envelope.

## I/O contract

- **Input** = `GraphInput`: the `ScopedRequest` fields this stage reasons over /
  threads through (`case_id`, `jurisdiction`, `request_id`, `subject`, `track`,
  `record_types`, `departments_hint`, `extracted_fields`) PLUS the injected
  `available_departments: list[str]` (the department universe, from
  `RecordStore.list_departments`). `case_id`, `request_id`, `subject`, and
  `available_departments` are required; the rest default so the agent is
  independently runnable from a fixture.
- **Output** = `SearchPlan`, a **thin agent-local envelope** `{ tasks:
  list[SearchTask] }`. The §10 stage-3 contract is `SearchTask[]` (a list), but a
  uipath/LangGraph OUTPUT schema must have an object root, so the graph wraps the
  list. `SearchPlan` **composes** the locked, shared `SearchTask` — it does not
  redefine it. Maestro / the downstream record-query read `output.tasks` and fan
  out over the real shared `SearchTask` items. It is deliberately **not** a new
  shared pipeline contract (that would be the IntakeResult mistake); see
  ASSUMPTIONS.md.

Each emitted `SearchTask` carries: identity (`case_id`/`jurisdiction`), the
deterministic `task_id`, `department` (verbatim one of the available
departments), and `terms: SearchTerms` (`keywords` + optional
`date_from`/`date_to`/`record_types`).

### Deterministic `task_id` (§8.5)

`task_id_for(department) = "search-<slug(department)>"`, where `slug` lowercases
and collapses every run of non-alphanumerics to a single `-`. It is:
- **deterministic** from the department name ⇒ stable across retries (the §8.5
  idempotency requirement for the downstream record-query side effect);
- **`:`-free** by construction, because the downstream
  `query_key(case_id, task_id)` uses `:` as its segment separator and rejects any
  segment containing it. `assemble_node` proves this by computing
  `query_key(case_id, task_id)` for every task and raising if it is malformed.

The `task_id` is namespaced by `case_id` only at the `query_key` boundary
(`query_key` returns `"<case_id>:query:<task_id>"`), so two different cases that
task the same department get distinct query keys, while one case re-tasking a
department replays the same key.

`task_id_for` is **not injective in general** — two distinct department names can
slug-collide (e.g. `"R&D"` and `"R/D"` → `"search-r-d"`), which within one plan
would make two `SearchTask`s share one §8.5 discriminator (the second
department's records would silently dedupe against the first at the idempotent
record-query boundary). `assemble_node` therefore **enforces injectivity** over
the chosen department set: a collision (two distinct departments → same
`task_id`) **raises `CustodianUnrecoverableError`** naming both departments and
the shared `task_id`. It is NOT re-prompted (re-prompting would wrongly pressure
the model to drop a legitimately-distinct department) and NOT auto-disambiguated
(a suffix derived from the present set would make a department's `task_id` depend
on which OTHER departments are tasked, breaking replay-stability). The seeded
universe is slug-distinct, so a collision signals a real input problem and
dead-letters to the human queue with full context (§8.2).

## §8.2 failure posture (raise, don't fake)

There is no structurally-valid-but-fake fallback. On an UNRECOVERABLE condition
the agent RAISES a module-level `CustodianUnrecoverableError(RuntimeError)`
carrying `case_id`, `request_id`, and the specific violation. Because the agent
runs as a Maestro Service Task, raising makes Maestro pause the case and route it
to the human dead-letter queue with full state preserved (§8.2) — it does **not**
emit a fake plan.

Unrecoverable conditions (raise):
- `available_departments` is empty — a **permanent** precondition (the universe
  is injected; an empty universe means the upstream `list_departments` returned
  nothing).
- Missing `ANTHROPIC_API_KEY` — a **permanent** precondition (§8.2 "permanent →
  no retry").
- LLM output still invalid after the one §8.3 re-prompt (schema OR the semantic
  constraint gate: empty plan / off-universe department / duplicate / empty
  keywords).
- A constructed `SearchTask` / `SearchTerms` failing boundary validation, or a
  `task_id` that is not a valid §8.5 query-key discriminator.
- A `task_id` **collision**: two distinct tasked departments slug to the same
  `task_id` (the §8.5 discriminator must be injective across the tasked set).

A department the model chooses **not** to task is a normal result, not a failure.
An unparseable `date_from`/`date_to` is also not a hard failure — the term simply
carries no date bound (both bounds are optional on `SearchTerms`).

## Import surface from `shared`

```python
from shared.contracts import (
    FEDERAL_FOIA, SearchTask, SearchTerms, query_key,
)
```

The agent does **not** redefine any contract. `_SearchPlanDraft` /
`_DepartmentPlan` are internal LLM-output schemas (not shared contracts):
deliberately narrower than `SearchTask`, carrying no identity fields and no
`task_id`, so the model can never fabricate a `case_id` or a non-deterministic
task id. `SearchPlan` is the thin agent-local OUTPUT envelope (see above).

## Hard rules honoured

- **One graph, one stage, no agent-to-agent calls, no Maestro logic in Python.**
- **No RecordStore I/O in the agent** — the universe is injected; the agent
  reasons over what it is given.
- **No per-department status** — `SearchTask` carries no respond/slow/silent/
  wrong_docs field; that is the RecordStore backing's concern at query time.
- **Deterministic, replay-stable `task_id`** keyed off the department (§8.5).
- **`jurisdiction` passed from day one**, threaded onto every `SearchTask`.
- **Fail by raising, not faking (§8.2).**

## Model-per-step config (§9, §13)

Model is a per-step config value, never hardcoded in business logic. `config.py`
exposes `model_for(step)`:

- resolution order: `<STEP>_MODEL` env var → the §9 default for that step →
  `DEFAULT_MODEL` (`claude-opus-4-8`).
- the one step is `plan` → `claude-opus-4-8` (env `CUSTODIAN_MODEL`).

LLM calls go **directly to Anthropic** via `langchain_anthropic.ChatAnthropic`
(brief §13 — not UiPathChat). The API key is resolved by
`main._resolve_anthropic_key()`: `ANTHROPIC_API_KEY` env first, then the
Orchestrator secret-asset fallback (`DisclosureFlow_AnthropicApiKey`, the
confirmed-working serverless-robot key path). The only model fallback is the
**config-time** chain inside `model_for` (`<STEP>_MODEL` env → §9 default →
`DEFAULT_MODEL`); it is applied BEFORE the client is built. There is **no
call-time model fallback** — if the resolved model id is unavailable, the
Anthropic client raises at invoke time, `plan_node` treats it as a
transport/parse error and runs ONE raw-JSON retry on the SAME model, then raises
`CustodianUnrecoverableError`. A permanent switch onto `DEFAULT_MODEL` (e.g. if
the §9 model were retired) is a config change, noted in `ASSUMPTIONS.md`.

> **Opus 4.8 + temperature.** `claude-opus-4-8` REJECTS the `temperature`
> parameter. `temperature_for` therefore returns `None` by default and
> `_build_llm` OMITS `temperature` from the client; a temperature is sent only
> when `CUSTODIAN_TEMPERATURE` is explicitly set (e.g. if a step is reconfigured
> onto a model that accepts it). This keeps the model id a pure config concern.

## Build / run (vendor-then-pack)

`shared/` is **vendored** into this dir at build time (it is a build artifact,
gitignored here; the canonical source is repo-root `shared/`). Always build via
the repo-root `Makefile` so the vendoring rsync cannot be skipped:

```bash
make vendor  AGENT=custodian-search-agent   # copy shared/ in
make init    AGENT=custodian-search-agent   # vendor + uipath init (regenerate schema; keeps AGENTS.md)
make pack    AGENT=custodian-search-agent   # vendor + init + uipath pack
make publish AGENT=custodian-search-agent   # vendor + init + pack + uipath publish
```

A bare `uipath pack` without vendoring would silently omit `shared/` and
ImportError on the robot. `pyproject.toml` declares `pydantic` explicitly (the
robot installs only what is listed) plus `langchain-anthropic` for the direct
Claude call.

## Local verification

```bash
make vendor AGENT=custodian-search-agent
cd agents/custodian-search-agent
# uipath run loads THIS dir's .env (empty); source the root .env for a live run:
set -a && . ../../.env && set +a
uv run uipath run agent --file fixtures/scoped_clean.json   # needs ANTHROPIC_API_KEY
```

Fixtures double as demo custodian inputs:
- `fixtures/scoped_clean.json` (Journey A): a specific procurement-email request
  with a single `departments_hint` → one `SearchTask` for "Office of Procurement"
  with date bounds from `extracted_fields`.
- `fixtures/scoped_broad.json` (Journey B): a broad IT-modernization request with
  no hint → all three departments tasked, each with its own terms.

Without `ANTHROPIC_API_KEY` (and with no readable Orchestrator asset) the run
RAISES `CustodianUnrecoverableError` (the §8.2 permanent-precondition path)
rather than returning a fake plan — on UiPath this is what routes the case to the
human dead-letter queue.
