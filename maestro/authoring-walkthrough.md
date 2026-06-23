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

---
---

# PART II — The execution guide (verified, do-this-exactly)

> Added 2026-06-23. This is the synthesized, click-level companion to Part I above. It was produced by cross-checking Part I and [`case-model-spec.md`](case-model-spec.md) against **current UiPath Maestro docs** (the Sources list) and the **real contracts** in [`shared/contracts/`](../shared/contracts/) + seams in [`shared/seams/`](../shared/seams/). Where Part I and this guide differ, **this guide reflects the doc-verified / contract-verified truth** (the conflicts are called out in §D). The spec's 4 GATE decisions remain LOCKED.
>
> **Read in this order:** §A (build order + what blocks what) → work the phases in §B → keep §C open while mapping Service-Task inputs/outputs → §D is the list of things Part I got slightly wrong, fix as you go.

## §A — Orientation: build order & blocker map

**What's already true** (don't re-do): all three agents are live in folder **`Shared`** (id `3083529`, key `257dab65-2353-4e0c-96e8-ff9f3746d9ed`) with a Default Serverless runtime; scoping **2232380**, custodian-search **2232377**, review-redaction **2232381** (thin pass-through). The `steps/` Python (record-query, release) is validated in `tests/`. None of the Maestro model, the Action Center apps, or the API Workflows exist yet.

**Net-new artifacts and exactly what each blocks** (build only what the next journey needs):

| Artifact | Status | Blocks | Does NOT block |
|---|---|---|---|
| `DisclosureFlow_RedactionReview` Action Center app | ❌ not built | Stage-4 interrupt gate; **Journey C** | Phases 0–5; **Journey A** |
| HITL re-deploy of `review-redaction-agent` (release id **will change**) | built, ❌ not deployed | real Stage-4 gate; **Journey C**; forces a Stage-4 **rebind** | Phase 3 (use thin **2232381**); **Journey A** |
| Close-out + final-release Action Center apps | ❌ not built | Phase 6 User Tasks; **Journey B** close-out; Stage-6 approval | Phases 0–5; **Journey A** |
| record-query API Workflow | ❌ not built (Python `steps/` works) | nothing critical — Python interim runs Journey A | first-green |
| release API Workflow | ❌ not built (Python `steps/` works) | nothing critical — Python interim runs Journey A | first-green |

**The build order to actually follow** (refines the spec's "Sequencing after approval"):

1. **Phase 0 → Phase 1 spike.** The spike pins every `[verify-in-Studio-Web]` name. Three things are genuinely unconfirmable from docs and MUST be pinned here: the **agent release/version selector**, the **case stage construct**, and the **mid-process requester-reply event** (the biggest unknown).
2. **Phases 2–3 → get Journey A green** on the **thin** agents + **Python-behind-seam** record-query/release. Needs **zero** net-new artifacts. This is the milestone that de-risks the whole platform — do not chase branches/HITL until this runs.
3. **In parallel (independent of Maestro authoring):** build the `DisclosureFlow_RedactionReview` action app + re-deploy the HITL Review agent → then **rebind the Stage-4 Service Task** to the new release id.
4. **Phase 6 (HITL) + Journey C.** Needs step 3 done.
5. **Phases 4–5 (branches + timers) + Journey B.** Needs the requester-reply event confirmed + the close-out app built.
6. **Re-implement record-query/release as API Workflows** and rewire those two Service Tasks (non-blocking Platform-Usage upgrade; export under `workflows/`).

**Two rebind hazards to remember:** (1) the Stage-4 release id changes on HITL re-deploy — rebind + republish **before** running Journey C, or you silently invoke the thin agent (0 proposals) and the governance beat never fires. (2) If the release selector binds "latest" instead of a pinned id, confirm at the spike whether re-deploy auto-rebinds.

## §B — Phase-by-phase: what you click, in order

Checkboxes = the literal actions. "✅ doc-confirmed" = verified against current UiPath docs. "🔍 verify-in-SW" = pin it in the Phase-1 spike; the *what to look for* + *fallback* is given.

### Phase 0 — Create the project
- [ ] Automation Cloud → **Maestro** (or Studio Web → **New Project**).
- [ ] Pick a **case / case-management** project type distinct from "Process (BPMN)". 🔍 Look for a type named "Case" / "Case Management" / "Agentic Case". **If no distinct Case type exists → STOP, this is a decision GATE** (the BPMN fallback per platform-check row-1 / spec §11). Do not silently create a BPMN process and call it Case.
- [ ] Name it `DisclosureFlow`.

### Phase 1 — The GATE-1 spike (do FIRST, ~½ day) — runs entirely against the live scoping-agent
- [ ] Toolbox → drag **Start event**.
- [ ] Toolbox → drag **Service Task**; connect Start → Task with a **sequence flow**.
- [ ] Task → **Properties → Implementation → Action** dropdown → **`Start and wait for agent`** (✅ doc-confirmed action).
- [ ] In the agent selector pick **scoping-agent**. 🔍 **verify the release/version selector** (release **2232380**) — docs don't specify this UI. Look for a version dropdown; note whether it pins an id or binds "latest".
- [ ] **Inputs:** map one input (✅ inputs cross as a JSON payload). Note the exact JSON key names the panel expects.
- [ ] **Output > Response:** (✅ doc-confirmed section) map the agent response to a process variable. **Record the JSON nesting** — is it raw typed JSON or nested under `response`/`output`?
- [ ] Attach a **boundary timer event** to the task; set a short **Duration**. 🔍 confirm the **interrupting vs non-interrupting** toggle (solid vs dashed ring).
- [ ] Drop an **intermediate / message catch event** + a **`Wait for connector event`** Service Task and test whether an external API call resumes a *running* instance. 🔍 **This is the highest-risk pin** (see §D-2).
- [ ] **Publish**, run one instance, watch it invoke the real scoping-agent job in `Shared` on the timeline.

**Record back into spec §11 from the spike:** the **stage construct** (look for stage/phase lanes or a "stage manager" panel; fallback: the blog confirms stages exist — if the canvas is flat BPMN with no stage lanes, flag it); how a stage advances on a Service-Task completion; the **release selector**; the **`Output > Response` nesting**; **boundary timer interrupting/non-interrupting**; and the **requester-reply event** mechanism.

> **De-risk the agent-output read (applies everywhere):** OutputArguments lag a few seconds after `State=Successful` (platform-check). The native `Start and wait for agent` task is *designed* to complete on the job-**completion event**, so it does the right thing — the lag only bites custom glue that polls `State`. **Spike check:** confirm the output variable is populated the instant the task shows complete; if it reads empty, the task is reading State, not the completion payload — escalate before building the spine on it.

### Phase 2 — Define the case data object
- [ ] Open the model's **Variables / Data** panel.
- [ ] Create: `case.request`, `case.scoped`, `case.search_plan`, `case.available_departments`, `case.query_results`, `case.candidates`, `case.review`, `case.approved`, `case.release`, `case.clock`, `case.identity`. (Exact types in §C-1.)
- [ ] 🔍 verify whether variables can be **typed objects/schemas** (ideal) or only **JSON strings**. Either works — contracts cross as JSON (`model_dump(mode="json")` / `model_validate_json`), and `extra="forbid"` catches drift at the agent boundary regardless.
- [ ] Set `case.identity.jurisdiction = "federal_foia"` (constant). Wire `case.identity.case_id` = the Maestro **case instance id**.
- [ ] ⚠ Read §D-1/§D-3/§D-4 now — `search_plan`, `review`, `clock`, `identity` are not all what Part I implies.

### Phase 3 — Build the spine (Journey A happy path) — ZERO net-new artifacts
- [ ] **Stage 1 Intake** — Service Task → `Start and wait for agent` → scoping-agent (**2232380**). Inputs: `case.request` fields **+ sibling `{case_id, jurisdiction}`** (not added onto the Request object — §D-5). Output > Response → `case.scoped`.
- [ ] **Stage 2 Triage** — no element; reads `case.scoped.track` / `.is_vague` (gateway is Phase 4).
- [ ] **pre-3a dept inject** — small Service Task. Interim: **`Execute script`** (✅ doc-confirmed action) wrapping `RecordStore.list_departments("federal_foia")`. Output → `case.available_departments`.
- [ ] **Stage 3a Search tasking** — Service Task → `Start and wait for agent` → custodian-search-agent (**2232377**). Inputs: `case.scoped` **+ sibling `available_departments[]`**. Output > Response → `case.search_plan` (a **`SearchTask[]`** — §D-1).
- [ ] **Stage 3b Record query** — for first-green, a single **`Execute script`** that loops `case.search_plan.tasks[]` via `record_query_step.py` and returns `case.query_results`; then flatten `QueryResult.records` → `case.candidates`. **Defer true multi-instance** (unconfirmed — §D-2); don't block Journey A on it.
- [ ] **Stage 4 Review** — Service Task → `Start and wait for agent` → review-redaction-agent (**2232381, thin** — do NOT swap in the HITL build yet). Inputs: `case.candidates` **+ identity incl. `officer`** (§D-4). Output > Response → `case.review`. On Journey A this returns **0 proposals**; no interrupt fires.
- [ ] **Stage 6 Release** — interim **`Execute script`** wrapping `release_step.py` (already runs the §8.4 guard). Output → `case.release`. (The §3c User Task comes in Phase 6.)
- [ ] Connect with sequence flows. **Publish**, run Journey-A seed, watch **Intake → Search → Query → Review(0) → Release** on the timeline. **← first green milestone.**

### Phase 4 — Exception branches (gateways)
- [ ] **Vague gateway (§5.A):** after Stage 1, toolbox → **Exclusive gateway** → **Conditions → Expression editor** → `case.scoped.is_vague == true` (✅ gateway + `==` syntax doc-confirmed).
  - `false` → Stage 3a. `true` → clarification branch: send-clarification step (idempotent on `clarification_round` — §C-4) → start tolling + arm grace timer (Phase 5) → **wait on requester-reply event** → on reply, re-invoke scoping-agent (increment `clarification_round`) → re-evaluate.
- [ ] **Custodian-status branches (§5.B):** Exclusive gateway on each `QueryResult.status`: `responded` → forward; `slow` → reminder timer → wait; `silent` → reminder → second-timeout **escalate** (User Task / Action Center task); `wrong_docs` → forward (Review marks `is_responsive=False`). **Rename the connector lines** ("responded"/"slow"/"silent").
- [ ] **Do NOT author reject→revise** — it lives inside the Review agent's interrupt loop (§5.C).
- [ ] 🔍 **requester-reply event** is the riskiest construct — docs confirm only a Message **start** event + a `Wait for connector event` action, **not** a mid-process message catch (§D-2). Pin the mechanism at the spike before relying on it.

### Phase 5 — Timers: clock + grace + reminders
- [ ] **20-working-day clock:** on case open, an early step calls `Clock` → set `case.clock.deadline` via `Clock.add_business_days(now, 20)`. Attach a **non-interrupting** timer at the deadline that **raises a deadline-risk flag on the timeline — never closes the case** (hard rule).
- [ ] **Tolling — ⚠ NOT a "pause timer" (§D-6):** Maestro timers **don't pause processes**. Tolling = the same step that enters the clarification branch calls **`Clock.pause()`** (math seam stops working-day accrual) while the case simply **waits at the requester-reply event**. On reply: disarm grace + **`Clock.resume()`**. Keep `Clock.pause/resume` and the arm/disarm in **one step** (the only desync point).
- [ ] **30-working-day grace:** a separate **Duration/Date** timer armed when a clarification is sent; if it fires before the reply → **route to the close-out User Task** (Phase 6). Configurable case parameter (default 30). This timer **re-routes** (matches docs).
- [ ] **Custodian reminders (§5.B):** **non-interrupting** boundary timers for laddered reminders + one **interrupting** timer for escalation (✅ doc-confirmed "laddered reminders + one interrupting closure" pattern).

### Phase 6 — The two HITL gates that ARE Maestro elements
**Prereq for the real Stage-4 gate:** the `DisclosureFlow_RedactionReview` app exists AND the HITL Review agent is re-deployed AND the Stage-4 Service Task is **rebound** to the new release id.
- [ ] **Close-out User Task (§3b):** toolbox → drag → **Change element → User task** → **Implementation → Action → `Create action app task`** (✅ doc-confirmed) → select the close-out app → map case context. The decision returns via the **`hitlTask` output variable** (✅ doc-confirmed) → map it to a case variable (close/extend/re-open). Wire as the grace-timer target.
- [ ] **Final-release User Task (§3c):** another **User task** with `Create action app task` → final-release app, placed **before** the release step. Approves the **assembled package** (distinct from approving individual redactions). The §8.4 guard runs **after** it (inside the release step/workflow): missing token or any hash mismatch → **BLOCK** → route to human, never release.
- [ ] **Redaction gate (§3a) — confirm, don't author:** verify the Stage-4 Service Task (now the HITL agent) shows a distinct **"paused / waiting on Action Center"** state on the timeline when the interrupt fires — not "Running/stuck" (§D-7). Add a domain timeline entry ("redaction sent for approval") so the pause reads as intentional.

### Phase 7 — Publish & run
- [ ] **Publish** the model.
- [ ] Orchestrator → **Automations** tab → **Add process** → target folder **`Shared`** → configure connections → **Create**.
- [ ] Start a **Journey A** instance → watch Intake → Search → Query → Review(0) → Release on the dashboard/timeline.
- [ ] **Before Journey C:** re-deploy HITL agent → **rebind Stage-4** to the new release → republish. Then run Journey C (interrupt → Action Center → resume).
- [ ] **Journey B** last (branches + timers + requester-reply event).

## §C — Data-mapping reference (keep open while wiring Service Tasks)

All contracts cross as JSON: producer `model_dump(mode="json")`, agent `model_validate_json`. `ContractModel` is `extra="forbid"` — any unknown top-level field fails at the boundary.

### §C-1 Case variable → real type

| Case variable | Real type | Notes |
|---|---|---|
| `case.request` | `Request` | **pre-identity** — no `case_id`/`jurisdiction` fields |
| `case.scoped` | `ScopedRequest` (`IdentityEnvelope`) | |
| `case.search_plan` | **`SearchTask[]`** (bare list) | **no `SearchPlan` contract exists** — §D-1 |
| `case.available_departments` | `list[str]` | from `RecordStore.list_departments("federal_foia")` |
| `case.query_results` | `list[QueryResult]` | |
| `case.candidates` | `list[CandidateRecord]` | = flatten of every `QueryResult.records` |
| `case.review` | `{proposals: RedactionProposal[], reviewed: CandidateRecord[]}` | IO **envelope, not a contract** — §D-3 |
| `case.approved` | `list[ApprovedRedaction]` | read off the **Stage-4** completion (not a separate element) — §D-8 |
| `case.release` | `ReleasePackage` | |
| `case.clock` | case-held state bag | `working_days_remaining` is **derived, not a Clock output** — §D-6 |
| `case.identity` | case-level bag `{case_id, jurisdiction, requester, officer}` | only `case_id` + `jurisdiction` ride the contracts — §D-4 |

### §C-2 Per-Service-Task: send / receive / write

- **Stage 1 (scoping 2232380):** SEND `Request` fields (`request_id, requester, text, submitted_at, attachments`) **+ sibling `case_id, jurisdiction`**. RECEIVE `ScopedRequest` (`case_id, jurisdiction, request_id, track, subject, extracted_fields, record_types, departments_hint, is_vague, clarification_round, clarification`). WRITE → `case.scoped`.
- **pre-3a:** CALL `RecordStore.list_departments(jurisdiction="federal_foia")` (signature takes **jurisdiction only**). WRITE → `case.available_departments`.
- **Stage 3a (custodian 2232377):** SEND `ScopedRequest` **+ sibling `available_departments[]`**. RECEIVE `SearchTask[]` (each: `case_id, jurisdiction, task_id, department, terms{keywords, date_from?, date_to?, record_types}`). WRITE → `case.search_plan`.
- **Stage 3b (record-query, per task):** SEND one `SearchTask` (+ idempotency key disc = `task_id`). Workflow wraps `RecordStore.query(jurisdiction, department, terms, task_id)` (seam does **not** take `case_id`). RECEIVE one `QueryResult` (`case_id, jurisdiction, task_id, department, status, records[]`). WRITE → `case.query_results`; flatten `records` → `case.candidates`.
- **Stage 4 (review 2232381→HITL):** SEND `CandidateRecord[]` **+ identity incl. `officer`** (officer is the interrupt `assignee` and `ApprovedRedaction.officer` — it is NOT on `CandidateRecord`). RECEIVE pre-interrupt `{proposals: RedactionProposal[], reviewed: CandidateRecord[]}`; post-interrupt `ApprovedRedaction[]`. WRITE → `case.review`, then `case.approved`.
- **Stage 6 (release):** SEND `ApprovedRedaction[]` + source records (+ idempotency key disc = `package_id`). RECEIVE `ReleasePackage`; §8.4 guard recomputes the package sha256 vs `package_hash` and verifies each `approved_content_hash` → BLOCK on any mismatch. WRITE → `case.release`.

### §C-3 Identity & `case_id` propagation
`IdentityEnvelope` = **exactly** `case_id: str` (strict) + `jurisdiction: str` (default `"federal_foia"`). Carried by every contract **from `ScopedRequest` onward** (`ScopedRequest, SearchTask, CandidateRecord, QueryResult, RedactionProposal, ApprovedRedaction, ReleasePackage`). The only pipeline contract WITHOUT it: `Request`. `PackStamp` (`pack_id, pack_version`) is added only on `RedactionProposal, ApprovedRedaction, ReleasePackage`. `case_id` = the Maestro case instance id, injected before Stage 1; the scoping agent stamps it onto `ScopedRequest`, and every downstream instance carries the same value.

### §C-4 Idempotency discriminators (`shared/contracts/idempotency.py`)
Key shape `"<case_id>:<action>:<discriminator>"`. Side-effecting steps: **send clarification** → `clarification_round` (≥1); **record query / reminder / escalation** → `task_id`; **release** → `package_id`. Compute at the boundary, check-then-act, upsert (not append).

## §D — Corrections to Part I (the plan above got these slightly wrong)

1. **Identity injection is sibling payload fields, not "injected into `case.request`."** `Request` is `extra="forbid"` with no `case_id`/`jurisdiction` fields — adding them onto `case.request.model_dump()` trips validation. Pass them as **sibling top-level fields** in the Stage-1 input JSON (`Request` fields + `{case_id, jurisdiction}`); the agent emits them on `ScopedRequest`.
2. **The requester-reply mid-process event is unconfirmed.** Docs confirm only a Message **start** event + a **`Wait for connector event`** action — not an intermediate message catch that wakes a running case from a portal callback. **Pin at the spike**; fallback is `Wait for connector event` + an external API resume, not an invented catch-event click path.
3. **`SearchPlan` and `ReviewResult` are NOT contracts.** `case.search_plan` holds a bare **`SearchTask[]`**; `case.review` holds an IO **envelope** `{proposals, reviewed}`. Validate the **members** (`SearchTask`, `RedactionProposal`, `CandidateRecord`) — there is no `SearchPlan`/`ReviewResult` Pydantic model to validate against.
4. **`case.identity` ≠ `IdentityEnvelope`.** The envelope carries only `case_id` + `jurisdiction`. `requester`/`officer` are case-level conveniences; in particular **`officer` must be passed explicitly into the Stage-4 input** (it is not on `CandidateRecord`).
5. **`jurisdiction` is a `str` constant `"federal_foia"`, not an Enum** — thread it into every seam call (`list_departments`, `query`, `Clock.*`) and it rides every contract from `ScopedRequest` on.
6. **Tolling is NOT a "Maestro tolling timer that pauses."** Docs: *timers don't pause processes — they remind (non-interrupting) or re-route (interrupting).* Tolling = **`Clock.pause()`** math while the case **waits at the requester-reply event**. Maestro holds durable timer *state* and re-routes; the Clock seam is what "pauses." (Spec §4 GATE-4 is actually correct; Part I's Phase-5 wording "arms the Maestro tolling timer" is the misleading bit.) Also: `case.clock.working_days_remaining` has no Clock method — derive it; the seam gives `deadline_status → on_track|at_risk|overdue` and takes `tolled_days` as an **input** from Maestro's tolling state.
7. **The multi-instance fan-out (Stage 3b) is unconfirmed in docs.** No fetched doc describes multi-instance / loop markers / all-settled completion. Use the Python loop for first-green; before relying on Maestro-level fan-out, spike it. **Safer fallback:** have the record-query API Workflow loop internally and return `QueryResult[]` in **one** call — satisfies the contract without Maestro fan-out.
8. **The paused-agent (Stage-4 interrupt) timeline appearance is unverified and demo-critical.** Docs don't describe how a paused agent job renders. Prove on the cloud HITL round-trip that it shows "waiting on Action Center," add a domain timeline entry, and narrate it in the demo so it doesn't read as a hang.
9. **Maestro Case is ~5–6 weeks old** (blog dates it ~2026-05-14, not ~06-16). Treat every case-specific construct name as **spike-confirmed, not doc-confirmed**.

### Doc-confirmed building blocks you can trust (don't spike these)
`Start and wait for agent` Service Task; inputs as JSON payload + **`Output > Response`** read-back; completion-on-finish; User Task **`Create action app task`** + **`hitlTask`** output variable; **Exclusive gateway → Conditions → Expression editor** with `==`; boundary timers **interrupting/non-interrupting** + **Duration/Schedule/Date** + the **laddered-reminders-plus-one-interrupting-closure** pattern; **`Execute script`** and **`Start and wait for API workflow`** actions; Maestro Case as a stage-driven living entity.
