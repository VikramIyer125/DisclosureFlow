"""Custodian/Search coded agent — DisclosureFlow stage 3 (brief §3, §9, §10).

ONE single-purpose LangGraph graph that performs exactly one stage's reasoning:
given a `ScopedRequest` and the available department universe, it (1) picks the
SUBSET of departments to task and (2) generates per-department `SearchTerms`. It
returns a typed list of `SearchTask`s, each with a DETERMINISTIC `task_id` that
is the §8.5 idempotency discriminator for the downstream record-store query.

This agent is a PURE REASONING UNIT. It does NOT run the record-store queries, it
does NOT execute the fan-out, and it does NOT set per-department behavior/status
(respond/slow/silent/wrong_docs — that is the RecordStore demo backing's concern
at query time; `SearchTask` has no status field). It produces the *plan*; Maestro
fans the plan out across the RecordStore seam (an API Workflow downstream).

HARD RULES honoured here (CLAUDE.md / build-prompt §1a):
  * This is NOT a supervisor. It never calls the other agents and contains no
    cross-stage sequencing/routing. The fan-out, the slow/silent-custodian
    reminder→escalation branches, and the retry/dead-letter routing all live in
    the Maestro case model, not in this graph.
  * It does no RecordStore I/O. The department universe is INJECTED on the
    GraphInput as `available_departments` (Maestro sources it upstream from
    `RecordStore.list_departments`); the agent reasons over what it is given.
  * `case_id` / `jurisdiction` are threaded from the inbound `ScopedRequest`
    onto every emitted `SearchTask` via the `IdentityEnvelope` (day-one param).

Graph shape (linear, single stage):
    START → plan (LLM, Opus per §9) → assemble (typed-validate, §8.3) → END

I/O contract:
    input  = GraphInput  (the locked `ScopedRequest` fields + the injected
             `available_departments`)
    output = SearchPlan  (a thin AGENT-LOCAL envelope: `{ tasks: list[SearchTask] }`)

Output-shape note: the §10 stage-3 contract is `SearchTask[]` (a list), but a
uipath/LangGraph OUTPUT schema must have an object root. `SearchPlan` is a thin
agent-local envelope that COMPOSES the locked `SearchTask` without redefining it
— Maestro/the downstream record-query read `output.tasks` and fan out over the
real shared `SearchTask` items. It is deliberately NOT a new shared pipeline
contract (that would be the IntakeResult mistake); see ASSUMPTIONS.md.

§8.2/§8.3 failure posture: there is no structurally-valid-but-fake fallback. The
agent parses the LLM output into the constrained `_SearchPlanDraft` at the
boundary (§8.3); on the FIRST validation failure it re-prompts once with the
specific violation, and if the second attempt still fails — or a permanent/auth
precondition is missing (e.g. no `ANTHROPIC_API_KEY`, §8.2 "permanent → no
retry"), or there are no available departments to task — it RAISES
`CustodianUnrecoverableError`. Because the agent runs as a Maestro Service Task,
the raised failure makes Maestro dead-letter the case to a human queue with full
state preserved (§8.2). A department the model chooses NOT to task is a normal
result, not a failure; an empty plan over a NON-empty universe is also raised
(the agent must task at least one department when records could exist).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from shared.contracts import (
    FEDERAL_FOIA,
    SearchTask,
    SearchTerms,
    query_key,
)

from config import model_for, temperature_for


# ─────────────────────────────────────────────────────────────────────────────
# Unrecoverable-failure signal (§8.2)
# ─────────────────────────────────────────────────────────────────────────────


class CustodianUnrecoverableError(RuntimeError):
    """Raised when the Custodian/Search step cannot produce a valid SearchPlan.

    Per §8.2 this is the *unrecoverable* path: a permanent precondition is
    missing (e.g. `ANTHROPIC_API_KEY` not set — "permanent → no retry", or no
    `available_departments` to task at all), or the LLM output is still invalid
    after the one §8.3 re-prompt. The agent runs as a Maestro Service Task, so
    raising (rather than emitting a fake but structurally-valid plan) is what
    makes Maestro pause the case and route it to the human dead-letter queue with
    full state preserved. The message carries the `case_id` and `request_id`
    plus the specific violation for that queue.
    """

    def __init__(self, case_id: str, request_id: str, reason: str) -> None:
        self.case_id = case_id
        self.request_id = request_id
        self.reason = reason
        super().__init__(
            f"Custodian/Search unrecoverable for case '{case_id}' "
            f"(request '{request_id}'): {reason}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic task_id (§8.5 idempotency discriminator)
# ─────────────────────────────────────────────────────────────────────────────

# task_id scheme: "search-<slug(department)>". DETERMINISTIC from the department
# name so it is stable across retries (the §8.5 idempotency requirement). It must
# contain NO ':' because the downstream `query_key(case_id, task_id)` uses ':' as
# its segment separator and rejects any segment containing it. The slug lowercases
# and collapses every run of non-alphanumeric characters to a single '-'. The
# task_id is namespaced by case_id at the query-key boundary (query_key returns
# "<case_id>:query:<task_id>"), so two cases tasking the same department still get
# distinct query keys while one case re-tasking a department replays the same key.
#
# `task_id_for` is NOT injective in general: distinct department names can slug to
# the same id (e.g. "R&D" and "R/D" → "search-r-d"). Within a single plan that
# would make two SearchTasks share one §8.5 discriminator, so `assemble_node`
# enforces injectivity over the chosen department set and RAISES on a collision
# (the seeded universe is slug-distinct; a collision signals a real input problem).


def task_id_for(department: str) -> str:
    """Deterministic, replay-stable task id for a department (feeds §8.5 query key)."""
    slug = re.sub(r"[^a-z0-9]+", "-", department.strip().lower()).strip("-")
    if not slug:
        # A department that slugs to empty (e.g. all punctuation) cannot produce a
        # safe, distinguishable key. Surface as a hash-free deterministic fallback
        # so the id stays stable and ':'-free.
        slug = "dept"
    return f"search-{slug}"


# ─────────────────────────────────────────────────────────────────────────────
# Graph input model
# ─────────────────────────────────────────────────────────────────────────────


class GraphInput(BaseModel):
    """Inbound payload for the custodian/search agent.

    Composes the locked `ScopedRequest` fields (the case's interpreted scope)
    PLUS the injected `available_departments` — the department universe Maestro
    supplies, sourced upstream from `RecordStore.list_departments`. The agent
    does NO RecordStore I/O itself; the universe arrives as input so the agent
    stays a pure reasoning unit (and independently runnable from a fixture).

    Only the fields this stage actually reasons over / threads through are
    surfaced; the full `ScopedRequest` is not re-imposed because Maestro hands
    the Service Task exactly this shape. Defaults keep the agent runnable from a
    bare fixture.
    """

    case_id: str = Field(description="Maestro case id; threaded onto every SearchTask.")
    jurisdiction: str = Field(
        default=FEDERAL_FOIA,
        description="Legal regime; only 'federal_foia' in MVP, passed as a real param.",
    )
    request_id: str = Field(description="Originating Request.request_id (for audit / dead-letter context).")
    subject: str = Field(description="Normalized subject of the request (the search target).")
    track: Literal["fast_track", "standard", "complex"] = Field(
        default="standard", description="Triage track from scoping; informs breadth of tasking."
    )
    record_types: list[str] = Field(
        default_factory=list, description="Record types in scope (e.g. ['email','memo']); may be empty."
    )
    departments_hint: list[str] = Field(
        default_factory=list,
        description="Departments the requester explicitly named; a strong hint, not binding.",
    )
    extracted_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Normalized fields (date_from/date_to/parties/subject_detail) to seed search terms.",
    )
    available_departments: list[str] = Field(
        description="The department universe to choose from (injected; from RecordStore.list_departments)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal LLM structured-output schema (NOT a shared contract)
# ─────────────────────────────────────────────────────────────────────────────


class _DepartmentPlan(BaseModel):
    """The model's plan for ONE department it chose to task.

    Deliberately carries only what the model decides — a department name (which
    MUST be one of the injected `available_departments`) and the search terms.
    It carries no identity fields and no `task_id`: the `task_id` is DERIVED
    deterministically from the department in `assemble_node`, never invented by
    the model, so it stays replay-stable for the §8.5 key.
    """

    department: str = Field(description="Department to task. MUST be one of the available departments.")
    keywords: list[str] = Field(
        default_factory=list,
        description="Free-text search keywords for this department (subject + parties + record cues).",
    )
    record_types: list[str] = Field(
        default_factory=list,
        description="Optional record-type filter for this department (subset of the request's record_types).",
    )
    date_from: Optional[str] = Field(
        default=None, description="Optional ISO-8601 inclusive lower date bound, if the scope implies one."
    )
    date_to: Optional[str] = Field(
        default=None, description="Optional ISO-8601 inclusive upper date bound, if the scope implies one."
    )


class _SearchPlanDraft(BaseModel):
    """Constrained shape the planning LLM must return.

    `departments` is the SUBSET of the available universe the model chose to
    task, each with its generated terms. Never carries identity or task ids.
    """

    departments: list[_DepartmentPlan] = Field(
        description="The chosen subset of departments to task, each with its search terms."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Output envelope (thin, agent-local; composes the locked SearchTask)
# ─────────────────────────────────────────────────────────────────────────────


class SearchPlan(BaseModel):
    """Thin agent-local OUTPUT envelope wrapping the §10 `SearchTask[]` list.

    The §10 stage-3 contract is `SearchTask[]`, but a uipath/LangGraph OUTPUT
    schema needs an object root — so the graph emits `SearchPlan{tasks:[...]}`.
    This COMPOSES the locked, shared `SearchTask` (it does not redefine it):
    Maestro / the downstream record-query read `tasks` and fan out over the real
    shared items. Deliberately agent-local, not a new shared pipeline contract.
    """

    tasks: list[SearchTask] = Field(
        description="The custodian fan-out plan: one SearchTask per chosen department."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graph state
# ─────────────────────────────────────────────────────────────────────────────


class State(BaseModel):
    """Internal state flowing between nodes."""

    # Echoed inbound fields.
    case_id: str
    jurisdiction: str = FEDERAL_FOIA
    request_id: str
    subject: str
    track: Literal["fast_track", "standard", "complex"] = "standard"
    record_types: list[str] = Field(default_factory=list)
    departments_hint: list[str] = Field(default_factory=list)
    extracted_fields: dict[str, str] = Field(default_factory=dict)
    available_departments: list[str] = Field(default_factory=list)

    # Produced by the plan node.
    draft: Optional[_SearchPlanDraft] = None


# ─────────────────────────────────────────────────────────────────────────────
# Prompting
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the Custodian/Search planner for a U.S. federal FOIA \
(Freedom of Information Act) request-processing system. FOIA runs on a presumption of \
openness: your job is to plan a search that FINDS the responsive records so they can be \
disclosed — cast a search wide enough to be thorough, not narrow enough to miss things.

You are given the interpreted scope of a request and the CLOSED list of department \
repositories available to search. Decide:

1. Which departments to task. Choose the SUBSET of the available departments whose \
records could plausibly contain responsive material, reasoning from the subject, the \
record types, and any departments the requester named (departments_hint is a strong \
signal but not binding — you may add others the subject implicates, and you may task \
ALL available departments if the subject is broad). Every department you name MUST be \
one of the available departments, spelled EXACTLY as given. Do not invent departments.

2. For each chosen department, generate SearchTerms:
   - keywords: concrete free-text terms a repository search would match — pull from the \
subject, any named parties, contract/record identifiers, and record-type cues. Make them \
specific enough to be useful and broad enough not to miss obvious variants.
   - record_types: an optional filter, a subset of the request's record types (omit/empty \
if the request did not constrain record types).
   - date_from / date_to: ISO-8601 dates ONLY if the scope implies a time window (e.g. \
from extracted date fields); otherwise leave them null.

Task at least one department when any available department could hold responsive records. \
Return ONLY the structured object."""


def _human_prompt(state: State) -> str:
    return (
        f"Case id: {state.case_id}\n"
        f"Request id: {state.request_id}\n"
        f"Triage track: {state.track}\n"
        f"Subject:\n\"\"\"\n{state.subject}\n\"\"\"\n\n"
        f"Record types in scope: {state.record_types or 'unspecified'}\n"
        f"Departments the requester named (hint): {state.departments_hint or 'none'}\n"
        f"Normalized extracted fields: {state.extracted_fields or 'none'}\n\n"
        f"AVAILABLE departments (choose only from these, spelled exactly):\n"
        + "\n".join(f"  - {d}" for d in state.available_departments)
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
    works even when ``ANTHROPIC_API_KEY`` is not in the process environment.

    NOTE on model availability: `model_for(step)` already applies the only model
    fallback (env → §9 default → ``DEFAULT_MODEL``) at CONFIG time, before the
    client is built. There is NO call-time model fallback here: if the resolved
    model id is unavailable, the Anthropic client raises at invoke time, and
    `plan_node` treats that as a transport/parse error — it runs ONE raw-JSON
    retry on the SAME model and then raises ``CustodianUnrecoverableError``. It
    does not silently retry on a different model.

    ``temperature`` is OMITTED unless `temperature_for` returns a value — Opus
    4.8 (the §9 model here) rejects the ``temperature`` param, so the default is
    to not send it. It is included only when `CUSTODIAN_TEMPERATURE` is set.
    """
    from langchain_anthropic import ChatAnthropic

    kwargs = dict(
        model=model_for(step),
        max_tokens=2048,
        timeout=60,
        api_key=api_key,
    )
    temperature = temperature_for(step)
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Nodes (async per the required-structure conventions)
# ─────────────────────────────────────────────────────────────────────────────


async def plan_node(state: State) -> State:
    """Choose departments + generate per-department terms (Opus per §9).

    §8.3 posture: the model returns the constrained `_SearchPlanDraft`; on a
    parse/validation failure we RE-PROMPT ONCE with the specific violation. If
    the second attempt still fails we RAISE `CustodianUnrecoverableError` (§8.2
    unrecoverable) rather than emit an invalid or faked plan — Maestro then
    routes the case to the human dead-letter queue with full state. Structured
    output is requested via the model's native schema binding; a JSON-text
    fallback parser covers providers/paths that don't honour the binding.
    """
    if not state.available_departments:
        # Nothing to plan a search over: a permanent precondition failure (§8.2
        # "permanent → no retry"). The universe is injected by Maestro; an empty
        # universe means the upstream RecordStore.list_departments returned
        # nothing, which the agent cannot recover from. Raise → human queue.
        raise CustodianUnrecoverableError(
            state.case_id,
            state.request_id,
            "no available_departments were provided; the department universe is empty "
            "(permanent, §8.2) — the search plan cannot be built.",
        )

    api_key = _resolve_anthropic_key()
    if not api_key:
        # No key from env (.env / bound %ASSETS% env ref) AND none from the
        # Orchestrator asset fallback: a PERMANENT precondition failure (§8.2
        # "permanent → no retry"). Nothing to retry, so raise. Maestro routes the
        # case to the human dead-letter queue with full state preserved.
        raise CustodianUnrecoverableError(
            state.case_id,
            state.request_id,
            "ANTHROPIC_API_KEY unavailable (not in env and Orchestrator asset "
            f"'{ANTHROPIC_KEY_ASSET}' not readable); planning LLM step could not "
            "run (permanent, §8.2).",
        )

    llm = _build_llm("plan", api_key)
    structured = llm.with_structured_output(_SearchPlanDraft)
    base_messages = [SystemMessage(_SYSTEM_PROMPT), HumanMessage(_human_prompt(state))]

    last_error: Optional[str] = None
    for attempt in range(2):  # one initial try + one re-prompt (§8.3)
        messages = list(base_messages)
        if attempt == 1 and last_error is not None:
            messages.append(
                HumanMessage(
                    "Your previous response did not satisfy the required schema or constraints: "
                    f"{last_error}. Return ONLY a valid object whose departments are each EXACTLY "
                    "one of the available departments."
                )
            )
        try:
            draft = await structured.ainvoke(messages)
            if not isinstance(draft, _SearchPlanDraft):
                # Some bindings return a dict — validate it.
                draft = _SearchPlanDraft.model_validate(draft)
            err = _draft_constraint_error(draft, state)
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
            if parsed is not None and _draft_constraint_error(parsed, state) is None:
                return state.model_copy(update={"draft": parsed})

    raise CustodianUnrecoverableError(
        state.case_id,
        state.request_id,
        f"planning LLM could not produce a valid search plan after one §8.3 re-prompt: {last_error}",
    )


def _draft_constraint_error(draft: _SearchPlanDraft, state: State) -> Optional[str]:
    """Semantic checks beyond the Pydantic shape (§8.3 boundary, pre-assemble).

    Returns a human-readable violation string to drive the one re-prompt, or
    None if the draft is acceptable. Enforces: at least one department tasked,
    every chosen department is in the injected universe, no duplicate
    departments, and each tasked department has at least one keyword (an empty
    term set would make the downstream query meaningless).
    """
    if not draft.departments:
        return (
            "no departments were chosen, but the available universe is non-empty; "
            "task at least one department that could hold responsive records."
        )
    available = set(state.available_departments)
    seen: set[str] = set()
    for dep in draft.departments:
        if dep.department not in available:
            return (
                f"department {dep.department!r} is not one of the available departments "
                f"{sorted(available)}; choose only from the available list."
            )
        if dep.department in seen:
            return f"department {dep.department!r} is tasked more than once; task each department at most once."
        seen.add(dep.department)
        if not [k for k in dep.keywords if k and k.strip()]:
            return f"department {dep.department!r} has no non-empty keywords; generate at least one search keyword."
    return None


async def _raw_json_fallback(llm, base_messages) -> Optional[_SearchPlanDraft]:
    """Ask for raw JSON and parse it into `_SearchPlanDraft`; None if it still fails.

    Reached only from the broad `except` in `plan_node` — i.e. a transport/parse
    error from the native structured-output binding, not a schema/constraint
    failure. It re-sends the original prompt with an explicit JSON-shape
    instruction rather than `last_error`: there is usually no schema violation to
    echo here, the binding itself failed.
    """
    try:
        instruction = HumanMessage(
            "Respond with ONLY a JSON object of shape: "
            '{"departments": [{"department": str, "keywords": [str], '
            '"record_types": [str], "date_from": str|null, "date_to": str|null}]}. '
            "Each department must be exactly one of the available departments."
        )
        resp = await llm.ainvoke(base_messages + [instruction])
        content = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return None
        return _SearchPlanDraft.model_validate_json(content[start : end + 1])
    except Exception:
        return None


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an optional ISO-8601 date string into a datetime; None if absent/unparseable.

    A bad date string is not a hard failure for this stage — it simply means the
    model didn't supply a usable bound, so the term carries no date filter rather
    than blocking the whole plan. (`SearchTerms` date bounds are optional.)
    """
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def assemble_node(state: State) -> SearchPlan:
    """Assemble + boundary-validate the typed `SearchPlan` of `SearchTask`s (§8.3).

    Builds one `SearchTask` per chosen department from the model's draft PLUS the
    identity envelope (`case_id`/`jurisdiction`) threaded from the request
    context. The `task_id` is DERIVED deterministically from the department via
    `task_id_for` (never from the model), so it is replay-stable for the §8.5
    query key — verified here by computing `query_key(case_id, task_id)`, which
    also asserts the task_id is ':'-free and the key is well-formed.

    §8.3 boundary validation: constructing each `SearchTask`/`SearchTerms` runs
    their locked Pydantic contracts. A malformed value cannot leave this node —
    on a construction (validation) failure we RAISE `CustodianUnrecoverableError`
    so Maestro routes to the human dead-letter queue (§8.2); we never emit a
    faked-but-valid placeholder.
    """
    if state.draft is None:  # defensive: plan_node either set draft or already raised
        raise CustodianUnrecoverableError(
            state.case_id, state.request_id, "no search plan was produced by the plan step."
        )

    tasks: list[SearchTask] = []
    # §8.5 INJECTIVITY guard: `task_id_for` slugs the department, so two DISTINCT
    # department names that slug-collide (e.g. "R&D" and "R/D" → "search-r-d")
    # would derive the SAME task_id and produce two SearchTasks sharing one §8.5
    # discriminator — the second department's records would then silently dedupe
    # against the first at the idempotent record-query boundary. We require the
    # task_id to be INJECTIVE over the chosen department set. A collision is NOT
    # re-promptable (the model was asked for two legitimately-distinct departments
    # that happen to slug-collide; re-prompting would wrongly pressure it to drop a
    # valid one) and is NOT auto-disambiguated (a suffix derived from the present
    # department set would make a department's task_id depend on which OTHER
    # departments are tasked, breaking replay-stability). The seeded universe is
    # slug-distinct, so a collision signals a real input problem: raise →
    # Maestro dead-letters with full context (§8.2).
    task_id_owner: dict[str, str] = {}
    for dep in state.draft.departments:
        task_id = task_id_for(dep.department)
        prior = task_id_owner.get(task_id)
        if prior is not None and prior != dep.department:
            raise CustodianUnrecoverableError(
                state.case_id,
                state.request_id,
                f"task_id collision: distinct departments {prior!r} and "
                f"{dep.department!r} both derive task_id {task_id!r}; the §8.5 "
                "idempotency discriminator must be injective across the tasked "
                "departments (slug-distinct), so their records do not silently "
                "dedupe at the record-query boundary.",
            )
        task_id_owner[task_id] = dep.department
        try:
            # Validate the §8.5 key is well-formed (asserts task_id is ':'-free).
            query_key(state.case_id, task_id)
        except ValueError as exc:
            raise CustodianUnrecoverableError(
                state.case_id,
                state.request_id,
                f"deterministic task_id {task_id!r} for department {dep.department!r} "
                f"is not a valid §8.5 query-key discriminator: {exc}",
            ) from exc

        keywords = [k.strip() for k in dep.keywords if k and k.strip()]
        record_types = [r.strip() for r in dep.record_types if r and r.strip()]
        try:
            terms = SearchTerms(
                keywords=keywords,
                record_types=record_types,
                date_from=_parse_date(dep.date_from),
                date_to=_parse_date(dep.date_to),
            )
            tasks.append(
                SearchTask(
                    case_id=state.case_id,
                    jurisdiction=state.jurisdiction,
                    task_id=task_id,
                    department=dep.department,
                    terms=terms,
                )
            )
        except ValidationError as exc:
            raise CustodianUnrecoverableError(
                state.case_id,
                state.request_id,
                f"SearchTask/SearchTerms for department {dep.department!r} failed "
                f"boundary validation: {exc.errors()}",
            ) from exc

    if not tasks:
        # The draft passed plan_node's constraint check, so this is defensive: an
        # empty task list over a non-empty universe is unrecoverable (§8.2).
        raise CustodianUnrecoverableError(
            state.case_id,
            state.request_id,
            "the assembled search plan is empty; no department was tasked over a "
            "non-empty available universe.",
        )

    return SearchPlan(tasks=tasks)


# ─────────────────────────────────────────────────────────────────────────────
# Graph wiring
# ─────────────────────────────────────────────────────────────────────────────

builder = StateGraph(State, input=GraphInput, output=SearchPlan)
builder.add_node("plan", plan_node)
builder.add_node("assemble", assemble_node)
builder.add_edge(START, "plan")
builder.add_edge("plan", "assemble")
builder.add_edge("assemble", END)

graph = builder.compile()
