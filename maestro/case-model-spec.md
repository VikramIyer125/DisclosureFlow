# DisclosureFlow — Maestro Case Model Spec (Milestone 2: the case spine)

**Status:** PROPOSAL for human authoring in Studio Web. This is a written spec, not authored artifacts. Do not start authoring until the **GATE DECISIONS** at the end are approved.

**Scope of this doc:** the end-to-end case spine that wires the three deployed coded agents and the two mechanical steps through Maestro Case — request intake → scoping → custodian tasking → record query → redaction proposal → human approval → release check — plus the §2 exception branches, the §5 HITL split, the statutory clock, idempotency, and the §8.4 release guard. It is the Milestone-2 deliverable per brief §14.

---

## 0. Ground truth this spec builds on

**Deployed coded agents** (SHARED folder `3083529`, folder key `257dab65-2353-4e0c-96e8-ff9f3746d9ed`), each a "Start and wait for agent" Service-Task target:

| Agent | Release | Stage owned | IN contract | OUT contract |
|---|---|---|---|---|
| `scoping-agent` | 2232380 | 1–2 Intake/Triage | `Request` (+ `case_id`, `jurisdiction`) | `ScopedRequest` (with optional `clarification`) |
| `custodian-search-agent` | 2232377 | 3 Search tasking | `ScopedRequest` + `available_departments[]` | `SearchPlan{tasks: SearchTask[]}` |
| `review-redaction-agent` | 2232381 | 4 Review/Redaction | `CandidateRecord[]` + identity | `ReviewResult{proposals: RedactionProposal[], reviewed: CandidateRecord[]}` |

**Platform-check ground truth** (`docs/platform-check.md`): row 1 Maestro Case = PASS, row 2 "Start and wait for agent" → deployed coded agent = PASS, row 4 Action Center = PASS, row 5 API Workflows = PASS. These four PASSes are what make this spec authorable; this doc does not re-assert them from memory.

**Invoke transport** (already proven this session): Service Tasks / scripts start agent jobs via httpx `StartJobs` with header `x-uipath-folderkey` (raw curl is WAF-blocked, error 1010). **OutputArguments lag a few seconds after `State=Successful`** — the case model must read agent output on job *completion event*, not poll `State` and read immediately. Flagged in §9.

**Doc-verification note.** UiPath Maestro Case product docs are **not available through Context7** (the only "Maestro" library there is the unrelated mobile-UI-testing framework). The coded-agent HITL constructs below (`CreateAction`/`WaitAction`/`InvokeProcess` via `interrupt(...)`) **are** confirmed from current UiPath docs (`uipath-langchain-python/docs/human_in_the_loop.md`). Maestro-side construct names (stage manager, BPMN boundary/escape timer events, User Task element) are named from the brief and the tenant's verified capability, **not** from a fetched product-doc page — every such name is tagged **[verify-in-Studio-Web]** and listed in §11. None is load-bearing for the architecture; if Studio Web names a construct differently, the mapping holds.

---

## 1. The spine — 6 stages as Maestro stage-manager stages

The case is one Maestro **Case instance** carrying its data, participants, and timeline (brief §6). The lifecycle is governed by the Maestro **stage manager** **[verify-in-Studio-Web]**: six stages, stable in identity, dynamic in path. Each stage advances on its terminal step's completion event; branches and timers (§4–§6) re-route between stages without changing the stage set.

**Case data object.** A single typed case data document carries the pipeline contracts as the case advances. Each Service Task reads a slice and writes the next slice; nothing is recomputed out of band:

```
case.request          : Request           (seed, from portal)
case.scoped           : ScopedRequest     (Stage 1–2 out)
case.search_plan      : SearchPlan         → tasks: SearchTask[]   (Stage 3 out)
case.query_results    : QueryResult[]      (Stage 3 record-query out)
case.candidates       : CandidateRecord[]  (flattened from query_results, Stage 3→4 input)
case.review           : ReviewResult       → proposals: RedactionProposal[], reviewed: CandidateRecord[]  (Stage 4 out)
case.approved         : ApprovedRedaction[](Stage 5 out, post human gate)
case.release          : ReleasePackage     (Stage 6 out)
case.clock            : { deadline, working_days_remaining, tolling: bool, toll_started_at }
case.identity         : { case_id, jurisdiction="federal_foia", requester, officer }
```

`case_id` is the Maestro case instance id, injected into `Request` before Stage 1 so every downstream contract's `IdentityEnvelope.case_id` is the real case id (contracts require this from `ScopedRequest` onward).

### Stage-by-stage table

| # | Stage (stage-manager) | Construct(s) | Invokes | Receives (JSON in) | Emits (JSON out) | Advances on |
|---|---|---|---|---|---|---|
| 1 | Intake & perfection | Service Task **"Start and wait for agent"** | `scoping-agent` (2232380) | `Request` + `{case_id, jurisdiction}` | `ScopedRequest` (`is_vague`, optional `clarification`) | agent job completion event → write `case.scoped` |
| 2 | Triage & track | (folded into Stage 1 agent output) | — | — | `track` ∈ `case.scoped` | gateway on `case.scoped.is_vague` (see §5.A) |
| 3a | Search tasking | Service Task "Start and wait for agent" | `custodian-search-agent` (2232377) | `ScopedRequest` + `available_departments[]` (§2) | `SearchPlan{tasks: SearchTask[]}` | completion → write `case.search_plan` |
| 3b | Record query | **Multi-instance Service Task → API Workflow** (GATE 2) | `RecordStore.query` workflow, one instance per `SearchTask` | `SearchTask` (+ idempotency key) | `QueryResult` per task | all task instances settle / timeout (see §5.B) |
| 4 | Review & redaction | Service Task "Start and wait for agent" | `review-redaction-agent` (2232381) | `CandidateRecord[]` + identity | `ReviewResult{proposals, reviewed}` | completion → write `case.review` |
| 5 | Human review gate | **LangGraph interrupt INSIDE the Review agent** (§5.C, GATE 3) — *not* a separate Maestro task | (re-enters `review-redaction-agent`) | each `RedactionProposal` as Action Center action data | `ApprovedRedaction[]` (decision per proposal) | agent resumes & returns final `ReviewResult` |
| 6 | Release & production | **Maestro User Task** (final-release approval, §5.D) **then** Service Task → **API Workflow** (release) gated by §8.4 guard | release API Workflow | `ApprovedRedaction[]` + records | `ReleasePackage` | guard PASS → release completion event |

**Note on Stage 5 placement:** the redaction-approval gate is *inside* the Stage-4 agent, not a seventh Maestro element. Stage 4 (the Service Task) does not complete until the agent's internal interrupt loop is fully resolved and it returns approved decisions. The case model sees one long-running Service Task that pauses and resumes — Maestro's native pause/resume carries it. This is the deliberate §5 split (GATE 3).

---

## 2. Non-agent steps: `available_departments` injection + record query (GATE 2)

Two steps in Stage 3 are **deterministic, not agentic**. Both are mechanical integration work — exactly what the brief wants surfaced as API Workflows for Platform-Usage credit (brief §4).

### 2a. `available_departments` injection (before Stage 3a)
The custodian agent's input contract requires `available_departments[]`. These come from `RecordStore.list_departments()` (the seam), **not** from agent memory — the agent must only fan out to departments that exist. In the case model this is a small **API Workflow Service Task** (or a lightweight script step) placed *between* Stage 2 and Stage 3a: it calls `list_departments`, writes `case.available_departments`, and the Stage-3a Service Task passes that list into the agent's input JSON alongside `ScopedRequest`.

### 2b. Record query (Stage 3b)
Produces `CandidateRecord[]` for Review. Modeled as a **multi-instance Service Task** — one instance per `SearchTask` in `case.search_plan.tasks` — each instance invoking the **record-query API Workflow** which wraps `RecordStore.query(department, terms)` and returns one `QueryResult` (`status` ∈ `responded|slow|silent|wrong_docs`, `records: CandidateRecord[]`). The `status` field is what drives the §2 custodian exception branches (§5.B). After all instances settle, `case.candidates` = flatten of every `QueryResult.records`.

**Build sequencing (brief §14 item 6 / §4):** behind both steps sits **deterministic Python** (the `RecordStore` seam: `list_departments`, `query`). The journeys run end-to-end on that Python *first*. THEN — API Workflows confirmed available (platform-check row 5 PASS) — re-implement these two as real API Workflows in Studio Web and wire them as the Service Tasks above. Export the workflow definitions under `workflows/` as reference artifacts. **This is GATE 2: API-Workflow Service Task vs. another mechanism** — framed at the end.

---

## 3. HITL per gate (GATE 3 — split deliberately; never both for one gate)

Brief §5 / build-prompt item 7 split HITL by mechanism. Per gate:

### 3a. Redaction-approval gate → **LangGraph `interrupt` INSIDE the Review agent**
**Why interrupt, not a Maestro User Task:** the officer's accept/reject/edit must **re-enter the agent's reasoning** to drive the revise loop (§2: officer rejects → agent revises → re-review). A Maestro User Task would collect the decision *outside* the agent and could not feed it back into the same graph state without a supervisor pattern (forbidden). So the feedback-into-reasoning requirement forces the interrupt.

**Mechanism (confirmed, `uipath-langchain-python/docs/human_in_the_loop.md`):** the agent calls
```python
from uipath.models import CreateAction
decision = interrupt(CreateAction(
    name="<redaction-review-app>", title=f"Review redaction {record_ref}",
    data={...one RedactionProposal...}, assignee="<officer>"))
```
`interrupt(CreateAction(...))` **creates an Action Center task** populated with the proposal data and **pauses the agent job**; when the officer completes the action, the agent **resumes** with the action output (the decision) re-entered into graph state. The agent applies §2's revise loop internally and returns the final `ReviewResult` carrying `ApprovedRedaction[]`.

**⚠ REQUIRES CHANGING A DEPLOYED AGENT.** The currently deployed thin `review-redaction-agent` (release 2232381) **does not implement the interrupt** — it returns proposals straight through. To make the gate real it must be changed to: (1) per proposal (or per batch for high-confidence non-balancing ones), call `interrupt(CreateAction(...))`; (2) for `decision == "rejected"`/`"edited"`, loop the revision and re-interrupt; (3) emit `ApprovedRedaction[]` with `approval_token` + `approved_content_hash` (§8.4 inputs). **This is a sequenced change — see §10.** Until it lands, Stage 5 is a pass-through and Journey C's governance beat cannot run.

**Batching (§5):** high-confidence, non-balancing proposals may be grouped into one action; **Exemptions 6 and 7(C) (balancing tests) always get full individual review** regardless of confidence (§8.1a). The `ConfidenceSignal` on each proposal drives the grouping; it is derived, never LLM-set.

### 3b. Close-out decision → **Maestro User Task** (§5.A, §4)
When the requester-response grace window lapses with no reply, a timer routes the case to a **human close-out queue** as a Maestro **User Task** **[verify-in-Studio-Web]**. No feedback-into-reasoning is needed — it's a case-level governance decision (close / extend / re-open) — so a User Task is correct and an interrupt would be wrong. The clock never auto-closes (hard rule).

### 3c. Final-release approval → **Maestro User Task** (Stage 6)
A case-level governance gate with no reasoning feedback: the officer approves the assembled package for release. Modeled as a **Maestro User Task** preceding the release API Workflow. Distinct from 3a — 3a approves *individual redactions* (feeds reasoning); 3c approves *the assembled package* (governance). Two different gates, two correct mechanisms, never doubled on one gate.

---

## 4. The statutory clock + grace window (GATE 4 — clock math vs. Maestro timer state)

Two separate clocks, per brief §5:

**Backbone split (per the locked backbone decision): Clock seam = MATH, Maestro = STATE.**
- The **`Clock` seam** (injected, deterministic, demo = manual advance) computes *working-day math*: the 20-working-day deadline, working-days-remaining, tolling arithmetic (clock pauses while a clarification is outstanding), and deadline-risk status. It never reads wall-clock and never fires events.
- **Maestro holds timer STATE**: the durable long-running timers that *fire* on the case (tolling window, grace-window expiry) and the audit record of when each started/paused. Maestro's native long-running pause/resume + timeline is what survives across days (brief §6) — we do not rebuild a scheduler.

**(i) Federal FOIA response clock — 20 working days, tolling during clarification.**
On case open, the model sets `case.clock.deadline` from `Clock` (now + 20 working days). When the case enters the clarification branch (§5.A), the model **starts tolling**: it records `toll_started_at` and signals `Clock` to pause working-day accrual. On requester reply, tolling stops and `Clock` resumes accrual from where it paused. Deadline-risk status is read from `Clock` for the timeline; **the clock never closes the case** — at expiry it raises a deadline-risk flag on the timeline, nothing more.

**(ii) Requester-response grace window — configurable, default 30 working days.**
A **separate Maestro timer** **[verify-in-Studio-Web]** armed when a clarification is sent. If the requester does not reply before it fires, it routes the case to the **human close-out User Task** (§3b) — it does **not** close the case. Configurable (default 30 working days) as a case parameter.

---

## 5. Exception branches off the spine (§2)

### 5.A Vague scope → clarification loop (Stage 1/2)
Gateway on `case.scoped.is_vague`:
- `False` → straight to Stage 3a.
- `True` → **clarification branch**: (1) **send** the agent-drafted `clarification.message` to the requester — a side-effecting step (idempotent, §6); per §5 this is gated as a one-click human "send" or autonomous-but-logged; (2) the model **starts clock tolling** (§4-i) and **arms the grace-window timer** (§4-ii); (3) wait on a requester-reply event.
  - **Reply received** → tolling stops, `clarification_round` increments, re-invoke `scoping-agent` with the reply (a new Stage-1 pass), re-evaluate `is_vague`.
  - **Grace timer fires first** → route to **human close-out User Task** (§3b). Human decides close/extend. Never auto-close.

### 5.B Silent / slow / wrong custodian → reminder → escalation (Stage 3b)
Driven by `QueryResult.status` per task instance:
- `responded` → records flow forward (happy path).
- `slow` → a per-task **reminder timer** **[verify-in-Studio-Web]**; on fire, send reminder (idempotent, §6), wait again.
- `silent` → reminder; on a second timeout, **escalate** → a human/task queue (Maestro User Task or Action Center task) with full task context. Escalation is idempotent (§6).
- `wrong_docs` → records flow forward but Review will mark them non-responsive (`is_responsive=False`); not a failure (§8.2 legitimate-negative).

Per §8.2 the case **pauses and keeps state**, never dies, on any custodian exception.

### 5.C Officer rejects a redaction → agent revises → re-review (Stage 4/5)
Lives **inside** the Review agent's interrupt loop (§3a), not as a Maestro branch: `decision ∈ {rejected, edited}` re-enters the graph, the agent revises the proposal, and re-interrupts for re-review. The case model sees one long-running Stage-4 Service Task. The reject/edit is also written to the corrections log (advisory, §7 — never authoritative).

---

## 6. Idempotency — side-effecting steps + deterministic keys (§8.5)

Keys are computed **at the boundary** via the shared helper `shared/contracts/idempotency.py` (`idempotency_key(case_id, action, discriminator)`), never stored on contracts. Maestro's durable execution covers in-graph replay; these guard our **external** side effects so retries (§8.2) are safe.

| Side-effecting step | Helper | Key shape | Deterministic discriminator |
|---|---|---|---|
| Send clarification (§5.A) | `clarification_key(case_id, clarification_round)` | `<case>:clarification:round<N>` | `ScopedRequest.clarification_round` |
| Record query (§5.B, per task) | `query_key(case_id, task_id)` | `<case>:query:<task_id>` | `SearchTask.task_id` |
| Custodian reminder (§5.B) | `idempotency_key(case_id, "reminder", task_id)` | `<case>:reminder:<task_id>` | `task_id` |
| Custodian escalation (§5.B) | `idempotency_key(case_id, "escalation", task_id)` | `<case>:escalation:<task_id>` | `task_id` |
| Release (§8.4) | `release_key(case_id, package_id)` | `<case>:release:<package_id>` | `ReleasePackage.package_id` |

Each step does **check-then-act** on its key before the side effect; upserts not appends. (`clarification_round`, `task_id`, `package_id` are the contracts' own deterministic discriminators — already present on the data, so a replay computes the identical key.)

---

## 7. Release-integrity guard before production (§8.4)

The Stage-6 release API Workflow consumes **only** `ApprovedRedaction[]` that carry a valid `approval_token` (tied to the specific human approval from §3a) and whose `approved_content_hash` matches the bytes about to be released. The guard:
1. verifies every applied redaction's `approval_token` is present and well-formed (StrictStr — a garbled token cannot coerce truthy);
2. recomputes the assembled package bytes' sha256 and compares to `ReleasePackage.package_hash`;
3. verifies each `approved_content_hash` against the post-redaction bytes.

**Missing token or any hash mismatch → the guard returns a BLOCK (it does not raise)**, and the case model routes to a human (back to the §3c User Task or a dead-letter queue). Only approved bytes ever leave. The §3c User Task (final-release approval) sits *before* the guard; the guard is the deterministic backstop that the human approval is honest. Both are required — human intent **and** byte-level verification.

---

## 8. What Maestro covers natively — don't rebuild (§6)

- **Audit timeline.** Maestro's native case timeline records every stage transition, Service-Task invocation, User-Task decision, timer fire, and agent pause/resume. We add domain entries (clarification sent, custodian escalated, redaction approved, release blocked) but do **not** build a parallel audit store.
- **Long-running pause/resume.** Maestro's durable execution carries the case across the 20-day clock, the 30-day grace window, custodian-silence waits, and the in-agent interrupt pause. We rely on it rather than persisting/rehydrating state ourselves.
- **Exception routing / case-keeps-state.** §8.2's "the case pauses and keeps state, never dies" is Maestro's durable case instance, not a custom retry loop. We key/guard our own external side effects (§6); Maestro handles in-graph replay.

---

## 9. Wiring & runtime notes (carry into authoring)

- **Read agent output on the completion EVENT, not by polling `State`.** OutputArguments lag a few seconds after `State=Successful` (proven this session). The Service Task must consume the job-completion event/output, or the case data write will read stale/empty output.
- **Service-Task target / folder.** All three agents live in SHARED folder `3083529` (key `257dab65-...`); Service Tasks target them by release. Invoke transport is httpx `StartJobs` + `x-uipath-folderkey` (raw curl WAF-blocked 1010) — relevant if any glue script starts a job rather than the native Service Task.
- **Contracts cross the boundary as JSON.** `ContractModel` is configured `extra="forbid"` — a drifting producer fails loudly at the Service-Task boundary rather than silently dropping fields. Service Tasks send `model_dump(mode="json")` and the agent does `model_validate_json`.
- **Model per step is config**, never hardcoded (per-step config value, brief §9). Anthropic key is the Orchestrator asset `DisclosureFlow_AnthropicApiKey` (already created). LLM Gateway is an OPTIONAL stretch, not in this spine.

---

## 10. Changes to deployed agents this spine REQUIRES (sequence these)

1. **`review-redaction-agent` (2232381) — add the redaction-approval interrupt (§3a).** Currently a thin pass-through; must be changed to interrupt per proposal/batch via `interrupt(CreateAction(...))`, run the §5.C revise loop on reject/edit, and emit `ApprovedRedaction[]` with `approval_token` + `approved_content_hash`. **Blocks Journey C.** This is a coded-agent change (hand to `coded-agent-builder`), then re-`pack`/`publish`, before Stage 5 is real.
2. **`scoping-agent` (2232380) — confirm re-entry on clarification reply (§5.A).** Verify it accepts a prior-round reply and increments `clarification_round`. If the deployed thin version only does a single pass, it needs a re-scope path for Journey B. Lower priority than #1.
3. **`custodian-search-agent` (2232377) — confirm `available_departments[]` input.** Verify the deployed agent reads the injected department list (§2a) and does not invent departments. Likely already correct; confirm before Journey B.

The two **API Workflows** (record query, release) and the **Action Center review app** (the `name`/`key` that `CreateAction` targets) must also be authored before the respective stages are real — these are net-new platform artifacts, not agent changes.

---

## 11. Constructs named from the brief / tenant capability, NOT a fetched product-doc page — verify in Studio Web

UiPath Maestro Case product docs are not in Context7. The following are named from the brief + the tenant's verified PASS capabilities; confirm exact element names/behaviors in Studio Web before authoring. **None is architecturally load-bearing** — if a name differs, the mapping in §1 still holds.

- Maestro **stage manager** stages (§1) — confirm the case-stage construct and how a stage advances on a Service-Task completion event.
- Maestro **User Task** element (§3b, §3c) — confirm it surfaces in Action Center and how it writes its decision back to case data.
- **BPMN boundary / escape timer events** for tolling, grace-window expiry, custodian reminder/escalation (§4, §5.B) — confirm timer-event modeling on a stage/task and whether a timer can pause vs. re-route.
- **Multi-instance Service Task** over `SearchTask[]` (§2b) — confirm multi-instance (fan-out) on a Service Task and its completion semantics (all-settled).
- **Requester-reply event trigger** (§5.A) — confirm the external event mechanism (portal → Maestro) that wakes the clarification wait.

**Confirmed from current UiPath docs (`uipath-langchain-python/docs/human_in_the_loop.md`), not requiring Studio-Web verification:** `interrupt(CreateAction(...))` creates an Action Center task and resumes the agent on completion (§3a); `WaitAction` waits on an existing action; `InvokeProcess` lets an agent invoke an API Workflow/process and auto-resume. Agent job invocation via `processes.invoke` / `StartJobs` (§9).

---

# GATE DECISIONS FOR APPROVAL

Per CLAUDE.md, each gated choice is presented in two frames (Competency + Extensibility) with a recommendation. **Stop here for explicit approval before authoring.**

## GATE 1 — Case-model structure: 6 stage-manager stages with the §2 branches as the spine

**Options.**
- **(A) Maestro Case stage-manager, 6 stages, branches/timers between stages** (this spec). *Competency:* matches the Track-1 premise (case as living entity), gives native audit/pause/resume for the 20/30-day clocks and custodian waits, and the platform-check confirms Case is enabled. *Struggles:* Maestro Case construct names unverified against product docs (§11); the in-agent interrupt living "inside" Stage 4 is a slightly unusual shape (one long Service Task that pauses) and must be demoed clearly so it doesn't look like a stuck job.
- **(B) Maestro BPMN agentic process** (the platform-check fallback). *Competency:* still Automation Cloud + Maestro; gateways/events model the same dynamic path. *Struggles:* loses the case-as-entity framing and some native case-timeline/stage affordances — weaker against the Track-1 "Maestro Case" scoring; the brief makes this the *fallback only if Case is not enabled*, and Case **is** enabled (row 1 PASS).

**Extensibility.** (A) makes new stages/branches additive on the stage manager and keeps the case object as the single data spine — adding an appeals stage or a new exception branch is a model edit. (B) would force re-modeling the case framing later if we wanted true case management. (A) forecloses nothing (B) enables; (B) forecloses the case-timeline story.

**Recommendation: (A).** Case is enabled — using BPMN would discard the exact thing Track 1 scores. **One thing to weigh:** the §11 construct-name uncertainty — first authoring session should be a thin spike (one stage + one Service Task + one timer) to confirm names before building the full spine.

## GATE 2 — Record-query and release invocation mechanism: API-Workflow Service Tasks

**Options.**
- **(A) Both as real API Workflows, invoked as Maestro Service Tasks** (this spec); deterministic Python behind the `RecordStore`/release seams runs the journeys first, then re-implemented as workflows. *Competency:* exactly the brief's intent (§4) — surfaces deterministic integration distinct from the reasoning agents, scored under Platform Usage; API Workflows confirmed available (row 5 PASS). *Struggles:* a second authoring surface (Studio Web workflows) to build and wire; the record-query multi-instance fan-out (§2b) needs the workflow to return one `QueryResult` per call cleanly; more moving parts before Journey A is green.
- **(B) Keep them as deterministic Python tools invoked via a thin coded step / agent.** *Competency:* fastest to green journeys, fewest surfaces. *Struggles:* forfeits the Platform-Usage credit the brief explicitly wants; blurs the "platform doing deterministic integration" story; risks looking like everything is Python.
- **(C) Agent-invoked via `interrupt(InvokeProcess(...))`.** *Competency:* keeps invocation inside an agent. *Struggles:* wrong owner — these are case-level mechanical steps, not agent reasoning; would smuggle orchestration into an agent (against the no-supervisor spirit). Reject.

**Extensibility.** (A) lets the demo `RecordStore` (local/Drive folders) be swapped for a production connector by editing the workflow, not the case model — the seam already abstracts it. (B) keeps everything in Python, easy to change but invisible as platform usage. (A) is the extensible-AND-scored choice.

**Recommendation: (A), built in the brief's order** — Python-behind-seam first so journeys run, then re-implement as API Workflows and wire as Service Tasks; export under `workflows/`. **One thing to weigh:** the multi-instance fan-out for record query (§2b) is the riskiest workflow detail — validate that a Maestro multi-instance Service Task over `SearchTask[]` returns a per-task `QueryResult` cleanly during the GATE-1 spike.

## GATE 3 — HITL mechanism per gate (the deliberate split)

**The split (recommended), one mechanism per gate, never both:**

| Gate | Mechanism | Why this one / why not the other |
|---|---|---|
| **Redaction approval** (Stage 5) | **LangGraph `interrupt(CreateAction)` inside Review agent** | Decision must re-enter the agent to drive the revise loop (§5.C). A Maestro User Task collects the decision *outside* the agent — it could not feed reasoning without a forbidden supervisor. *Competency:* confirmed mechanism (docs); native revise loop. *Struggles:* requires changing the deployed agent (§10 #1); the paused Service Task must be demoed as intentional. |
| **Close-out** (grace lapse) | **Maestro User Task** | Case-level governance (close/extend); no reasoning feedback. *Struggles:* none material — it's the textbook User-Task case. |
| **Final-release approval** (Stage 6) | **Maestro User Task** | Governs the *assembled package*, not reasoning; precedes the §8.4 guard. *Struggles:* must be visibly distinct from redaction approval so the demo doesn't conflate "approve a redaction" with "approve the package." |

**Extensibility.** The split keeps reasoning-feedback gates in agents (swap the Action Center app without touching the case model) and governance gates in Maestro (add an appeal/review gate as another User Task without touching agent code). Collapsing everything into one mechanism would either drag governance into agents or force a supervisor for the revise loop — both foreclose later flexibility.

**Recommendation: adopt the split as tabled.** **One thing to weigh:** it forces the §10 #1 agent change before Journey C — sequence that change immediately after GATE approval so the hero journey isn't blocked late.

## GATE 4 — Clock/timer modeling: Clock seam = math, Maestro = timer state

**Options.**
- **(A) Split: `Clock` seam computes working-day math; Maestro holds durable timer state and fires events** (this spec, per the backbone decision). *Competency:* working-day/tolling math is unit-testable in Python against the injected `Clock` (demo manual-advance for deterministic recording); Maestro's durable timers survive the multi-day waits without a custom scheduler. *Struggles:* two sources that must agree — the model must signal `Clock` to pause/resume tolling exactly when it arms/disarms the Maestro tolling timer, or the displayed deadline drifts from the fired timer.
- **(B) All in Maestro timers** (no Clock seam math). *Competency:* one source. *Struggles:* working-day/tolling/grace math becomes un-unit-testable model config; breaks the brief's injected-`Clock` seam and the demo manual-advance ("time jump") affordance; the testing plan (brief §14) explicitly wants the 20-day/tolling/grace math tested against the seam.
- **(C) All in the Clock seam** (Python fires "timers"). *Competency:* fully testable. *Struggles:* re-implements durable long-running scheduling that Maestro already provides — exactly what §6 says not to rebuild; loses native timeline entries for timer fires.

**Extensibility.** (A) lets production swap the demo manual-advance `Clock` for a real clock with no model change, and lets a new jurisdiction's deadline rules ship as `Clock`/PolicyProvider data, not model edits. (B)/(C) each foreclose one of {testability, native durability}.

**Recommendation: (A).** It is the only option that keeps the math testable AND the long waits durable. **One thing to weigh:** the toll pause/resume handshake (model timer ↔ `Clock` accrual) is the one place the two can desync — make the model the single trigger (model arms/disarms timer *and* calls `Clock.pause/resume` in the same step) and assert their agreement in a test.

---

## Sequencing after approval (not a gate — for reference)
1. GATE-1 spike: thin Maestro Case (1 stage + 1 "Start and wait for agent" Service Task + 1 timer) to confirm §11 construct names.
2. Author the 6-stage spine with Python-behind-seam record-query/release; get Journey A green.
3. Change `review-redaction-agent` to add the interrupt (§10 #1); author the Action Center review app; get Journey C green.
4. Re-implement record-query/release as API Workflows; wire as Service Tasks; export under `workflows/`.
5. Confirm scoping re-entry + custodian `available_departments`; get Journey B green.
