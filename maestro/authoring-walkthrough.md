# DisclosureFlow — Maestro Case authoring walkthrough (Studio Web)

**Purpose:** the hands-on, click-by-click companion to [`case-model-spec.md`](case-model-spec.md). The spec is the *approved design* (its 4 GATE decisions are locked); this doc is *how to author it in Studio Web*. Where they disagree, the spec wins — flag the conflict.

**Audience:** Builder 1 ("the Spine") — the person authoring in Studio Web + Orchestrator/Action Center. This is single-author work: only one person touches the case model (it lives in the cloud, not git, and can't be merged).

**Status of UI labels:** Maestro Case is very new (announced ~2026-06-16); product docs confirm the BPMN building blocks and their property labels but are thin on the *case-specific* stage construct. The exact names tagged `[verify-in-Studio-Web]` in spec §11 are confirmed by the **Phase 1 spike** — do not skip it.

---

## Before you start — prerequisites

- **Tenant/folder:** staging `hackathon26_632`, Orchestrator folder **`Shared`** (id `3083529`, key `257dab65-2353-4e0c-96e8-ff9f3746d9ed`). All three agents already live here.
- **Agent release IDs** (Service Task targets):
  - scoping-agent — **2232380**
  - custodian-search-agent — **2232377**
  - review-redaction-agent — **2232381** *(will change when the interrupt/HITL version is re-deployed — rebind the Stage-4 Service Task then)*
- **Two external dependencies** that must exist for certain tasks (defer for Journey A):
  - `DisclosureFlow_RedactionReview` Action Center app (target of the Review agent's `interrupt`).
  - record-query + release **API Workflows** (run Python-behind-seam first; re-implement as workflows later).
- **Runtime/invoke facts** (from `docs/platform-check.md`): the `Shared` folder has a Default Serverless runtime assigned (jobs run, not Pending). Agent **output args lag a few seconds after `State=Successful`** — read agent output on the **completion event**, not by polling state. Contracts cross the boundary as JSON (`extra="forbid"`).

---

## Phase 0 — Create the project

1. Automation Cloud tenant → open **Maestro** → **Start modeling** (or Studio Web 2025.5+: **New Project → Process (Maestro)**).
2. If a **case / case-management** project type is offered as distinct from a plain BPMN process, choose it — Track 1 is "Maestro Case," and platform-check row 1 confirmed Case is enabled on this tenant.
3. Rename the project/process to `DisclosureFlow`.

---

## Phase 1 — The GATE-1 spike (do this FIRST, ~½ day)

A throwaway micro-model to confirm the `[verify-in-Studio-Web]` construct names before building the full spine on them.

1. **Add: Start event** (from the element toolbox).
2. **Add: Service Task** → Properties → **Implementation**:
   - **Action:** `Start and wait for agent`
   - **Automation:** select the deployed **scoping-agent** (release 2232380).
3. Map one input (**Inputs** section — variable/expression) and one output (**Output > Response** — assign to a process variable). Note the JSON payload shape.
4. Attach one **boundary timer event** to the task; confirm interrupting vs non-interrupting.
5. **Publish**, run one instance, watch it invoke the real agent.

**Verify and record back into spec §11:**
- the case **stage** construct and how a stage advances on a Service-Task completion event;
- agent output is readable on completion (output args lag after `Successful`);
- timer modeling (boundary vs intermediate; duration/date/cycle);
- the **requester-reply external event** mechanism (portal → Maestro) that wakes the clarification wait.

---

## Phase 2 — Define the case data object

Create the process/case variables that carry the pipeline (spec §1):

```
case.request           : Request            (seed, from portal)
case.scoped            : ScopedRequest      (Stage 1–2 out)
case.search_plan       : SearchPlan         → tasks: SearchTask[]   (Stage 3a out)
case.available_departments : string[]       (injected before Stage 3a)
case.query_results     : QueryResult[]      (Stage 3b out)
case.candidates        : CandidateRecord[]  (flatten of query_results; Stage 3→4 in)
case.review            : ReviewResult       → proposals, reviewed   (Stage 4 out)
case.approved          : ApprovedRedaction[](Stage 5 out, post human gate)
case.release           : ReleasePackage     (Stage 6 out)
case.clock             : { deadline, working_days_remaining, tolling, toll_started_at }
case.identity          : { case_id, jurisdiction="federal_foia", requester, officer }
```

- `case.identity.case_id` = the Maestro case instance id, injected into `case.request` before Stage 1 so every downstream contract's `case_id` is real.
- `jurisdiction` is the constant `"federal_foia"`.
- Each Service Task sends `model_dump(mode="json")`; the agent does `model_validate_json`. A drifting producer fails loudly at the boundary (`extra="forbid"`).

---

## Phase 3 — Build the spine (the happy path = Journey A)

Author in order. Each is a **Service Task** with **Action: "Start and wait for agent"** unless noted (spec §1 table). To change an element's type: select it → toolbox → **Change element** → pick the type.

| Stage | Element | Automation / Action | In → Out |
|---|---|---|---|
| 1 Intake | Service Task | scoping-agent (2232380) | `case.request` + `{case_id, jurisdiction}` → `case.scoped` |
| 2 Triage | folded into Stage 1 output | — | reads `case.scoped.track` / `.is_vague` |
| pre-3a: dept inject | small Service Task / API Workflow | `RecordStore.list_departments` | → `case.available_departments` |
| 3a Search tasking | Service Task | custodian-search-agent (2232377) | `case.scoped` + `available_departments` → `case.search_plan` |
| 3b Record query | **Multi-instance** Service Task → record-query API Workflow | one instance per `case.search_plan.tasks[]` | `SearchTask` → `QueryResult`; flatten → `case.candidates` |
| 4 Review | Service Task | review-redaction-agent (2232381) | `case.candidates` + identity → `case.review` (post-interrupt: `case.approved`) |
| 6 Release | **User Task**, then Service Task → release API Workflow | final-release approval, then guarded release | `case.approved` + records → `case.release` |

For each: map **Inputs** from the prior stage's output variable; map **Output > Response** to the next variable; connect with sequence flows.

> **First green milestone:** this straight-line path on Journey-A seed data, running end-to-end, before any branches. Stage 4 yields 0 proposals on Journey A, so no HITL is exercised.

---

## Phase 4 — Exception branches (gateways) — §2 / §5

1. **Vague scope (§5.A):** after Stage 1, **Add: Exclusive gateway** ("Is vague?") → **Conditions** → **Expression editor**: `case.scoped.is_vague == true`.
   - `false` → Stage 3a.
   - `true` → clarification branch: send clarification message (idempotent, §6) → start clock tolling + arm grace timer (Phase 5) → wait on requester-reply event → on reply, re-invoke scoping-agent (increment `clarification_round`) and re-evaluate.
2. **Custodian status (§5.B):** branch off each record-query instance's `QueryResult.status`:
   - `responded` → records flow forward;
   - `slow` → reminder timer, wait again;
   - `silent` → reminder, then on second timeout **escalate** (User Task / Action Center task);
   - `wrong_docs` → flow forward (Review marks `is_responsive=False` — a legitimate negative, not a failure).
   - Rename connector lines ("responded" / "slow" / "silent").
3. **Reject→revise is NOT a Maestro branch** — it lives inside the Review agent's interrupt loop (§5.C). Do not author it here.

Per §8.2 the case **pauses and keeps state** on any custodian exception — that is Maestro's durable case instance, not a custom retry.

---

## Phase 5 — Timers: statutory clock + grace + reminders — §4

Approved GATE-4 split: **Clock seam = working-day math; Maestro = durable timer state.**

1. **20-working-day FOIA clock:** on case open set `case.clock.deadline` from the Clock seam. Maestro holds the durable timer; at expiry it raises a **deadline-risk flag on the timeline — it never closes the case** (hard rule).
2. **Tolling:** entering the clarification branch, the *same step* arms the Maestro tolling timer **and** calls `Clock.pause()`. On reply, disarm + `Clock.resume()`. (This lockstep is the one place the two can desync — keep them in one step.)
3. **30-working-day grace window:** a separate timer armed when a clarification is sent; if it fires first → route to the **human close-out User Task** (Phase 6). Never auto-close. Configurable (case parameter, default 30 working days).
4. **Custodian reminders (§5.B):** **non-interrupting boundary timers** for laddered reminders + a final escalation (the documented "staged escalation / laddered timers" pattern).

---

## Phase 6 — The two HITL gates that ARE Maestro elements — §3

> The **redaction-approval gate is inside the Review agent** (LangGraph `interrupt`), not a Maestro element — the Stage-4 Service Task just pauses and resumes natively. It only requires the `DisclosureFlow_RedactionReview` Action Center app to exist.

The two **User Tasks**:

1. **Close-out (§3b):** **Change element → User task** → **Implementation → Action: "Create action app task"** → select the close-out app → map case context. Reached when the grace timer fires. Decision: close / extend / re-open.
2. **Final-release approval (§3c):** a **User Task before** the release API Workflow; approves the *assembled package* (distinct from approving individual redactions). The §8.4 release-integrity guard runs **after** it as the deterministic backstop: missing token or any hash mismatch → **BLOCK** (route to human), never release.

---

## Phase 7 — Publish & run

1. **Publish** the model.
2. Orchestrator → **Automations** tab → **Add process** (target folder `Shared`) → configure any connections → **Create**.
3. Start an instance with **Journey A** seed data; watch Intake → Search → Query → Review (0 proposals) → Release through the Maestro dashboard/timeline.
4. Then layer in **Journey C** (needs the interrupt agent + Action Center app) and **Journey B** (needs the branches + timers).

---

## What Maestro covers natively — don't rebuild (§6 / spec §8)

- **Audit timeline** — every stage transition, Service-Task invocation, User-Task decision, timer fire, agent pause/resume. Add domain entries (clarification sent, custodian escalated, redaction approved, release blocked); do not build a parallel audit store.
- **Long-running pause/resume** — carries the case across the 20-day clock, 30-day grace, custodian-silence waits, and the in-agent interrupt. Rely on it.
- **Exception routing / case-keeps-state** — §8.2's "pauses and keeps state, never dies" is the durable case instance. Key/guard only your own external side effects (§6).

---

## Honest caveats (from the doc-verification pass)

- The current docs did **not** spell out the exact selector for choosing an agent **release/version** inside the Service Task, nor the **case-specific stage** construct — both are exactly what the Phase-1 spike pins down.
- Record-query/release run **Python-behind-seam first** (already validated in `tests/`); they become real **API Workflow** Service Tasks later (non-blocking upgrade for Platform-Usage credit). For the first end-to-end you may wrap the Python step as an interim callable.

## Sources (current UiPath Maestro docs)

- [Implementing a complex process](https://docs.uipath.com/maestro/automation-cloud/latest/user-guide/how-to-complex-process)
- [Using agents in Maestro](https://docs.uipath.com/maestro/automation-cloud/latest/user-guide/using-agents-in-maestro)
- [Service task](https://docs.uipath.com/maestro/automation-cloud/latest/user-guide/service-task)
- [Time and reminders](https://docs.uipath.com/maestro/automation-cloud/latest/user-guide/time-and-reminders)
- [User task](https://docs.uipath.com/maestro/automation-cloud/latest/user-guide/user-task)
- [Introducing Maestro Case](https://www.uipath.com/blog/product-and-updates/introducing-maestro-case-new-uipath-capability)
