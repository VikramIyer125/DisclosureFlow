# Journey B — demo-day authoring runbook (Studio Web)

**Goal:** a live, demoable Journey B on the existing `DisclosureFlow` Maestro Case, showing the two human-click beats that make it the "dynamic case management" journey:

- **BEAT 1 — requester clicks:** vague request → scoping flags `is_vague` + drafts a clarification → an Action Center task asks the requester to narrow it → requester types a narrower request and submits → the case re-invokes scoping → now not-vague → proceeds.
- **BEAT 2 — officer clicks:** custodian fans out to 3 departments → record-query reports **Office of Communications = silent** → an Action Center task asks a records officer to handle it → officer clicks **Escalate** → the case proceeds to Review → Release.

**Roles:** you drive all Studio Web clicking. This doc + `workflows/record-query-b.js` are everything you paste/configure. Verified against the live `hackathon26_632 / DefaultTenant` tenant on 2026-06-29.

> Items the platform-integrator could not verify against the live Case UI are tagged **[CONFIRM-LIVE]** with a fallback. None is architecturally load-bearing.

---

## 0. Pre-flight (2 min)

- Be logged into **staging** Automation Cloud in the browser (the case lives in the cloud, not the CLI).
- The three agents are confirmed published in folder `Shared` (`3083529`): **scoping 2232380, custodian 2232377, review 2232381 (thin)**. Journey B uses scoping + custodian + the thin review — **no agent re-deploy needed.**
- Only if you re-publish a workflow via CLI: from the repo root run **`uipath auth --staging`** to refresh the ~1h token, then retry.
- **Snapshot/duplicate the current green Journey-A case if the UI allows.** Everything below is additive; the only thing that can break A is swapping its record-query script — so we use a **separate** `record-query-b` workflow and leave A's untouched.

---

## 1. Author the `record-query-b` API Workflow (~15 min)

This is the one new deterministic artifact. It returns the 3-department fan-out with one **silent** department so the case can branch. **Single call returning an array — do NOT use multi-instance fan-out** (undocumented/unverified in Maestro; the single-call array is the reliable, spec-blessed choice).

1. Studio Web → your solution → **New → API Workflow**, name it **`record-query-b`**.
2. Add a **`Script`** activity (JavaScript). Paste the entire body of **`workflows/record-query-b.js`** (this repo). It reads `$input.case_id` / `$input.jurisdiction` and `return`s `{ records, statuses, silent_department, has_silent }`. Use the **same return idiom as your working Journey-A record-query script** (it ends in `return {…}`).
3. Add a **`Response`** activity returning the script object via **`$context.outputs.<ScriptName>`** — grab the exact ref from **`{x} Insert variable`** (it's `Javascript_1`-style, **not** `.result`).
4. **Inputs:** `case_id` (String), `jurisdiction` (String).
5. **Output typing — critical:** `records` and `statuses` are arrays; every array output **must have a typed `Item`** (an untyped array breaks the solution pack). `silent_department` = String, `has_silent` = Boolean.
6. **Publish** to `Shared`.

---

## 2. PHASE 1 — Beat 1: requester clarification reply (~50 min, do first — highest risk)

The clickable-task construct (resolved): a **Maestro User task** whose Action = **"Create Action App task"**. It pauses the case for a human click and exposes a **`hitlTask`** output carrying the form response. This is the right tool (a case-level governance click), *not* an agent `interrupt`.

### 2.1 Build the Beat-1 App (the text-capture form) — verify this round-trips first

**The typed text round-trips in 3 moves** (verified against current UiPath Apps/Maestro docs). The key fact: the typed text comes back as an **Action schema Output PROPERTY** you define (named `requester_reply`) — **NOT** as the outcome and **NOT** as the control name. The Submit **outcome** (which button) and the **property value** (the typed text) are two different channels; you read the *property*.

> ⚠️ **Prerequisite:** `hitlTask` carries typed field values only on the **May 2026 Maestro release or later** (before that you could read only the outcome). If `hitlTask` doesn't expose field data, you're on an older release → use **Fallback 5** below.

Steps in the **Apps builder**:
1. Build/extend an **Escalation app** (same surface as the existing `SimpleApprovalApp`).
2. Form: a **read-only Text** bound to the `clarification` input (the message the requester sees) + an **editable Text input** control (e.g. `text1`) for the narrower request + a **Submit** outcome.
3. Open the **Action** control → **Action Properties** schema. **Add property** → name **`requester_reply`**, type **Text**, direction **In/Out** (In/Out lets you bind the control directly with zero rule lines — fewest moving parts).
4. **Bind the `text1` control's value directly to the `requester_reply` In/Out property.** (Alternative if you keep it Output-only: on **Submit → Submit Action rule → Input Override**, set `ActionProperties.<app>.requester_reply = MainPage.text1.Value` — one line.)
5. **Inputs the requester sees:** map `clarification` (or `clarification.message`) and `subject` from Stage-1 scoping.
6. **Publish the app**, then **run once and confirm the typed text comes back** before wiring anything else. (This is the single riskiest piece — prove it early. If it doesn't round-trip in ~15 min, drop to Fallback 5 without hesitation.)

**Verdict (verified):** a single scalar Text has **none** of the array/collection-expression friction that stuck the prior `decisions[]` app — no array `Item` typing, no per-row collection expression. This path is genuinely simpler.

### 2.2 Add the clarification User task
- In the Case **Plan**, after Stage-1 scoping: **Add task → Action** → Action = **"Create Action App task"** → target the Beat-1 app.
- **Entry rule:** **`is_vague == true`**; turn **"Run only once"** ON (so it doesn't re-fire after the reply clears `is_vague`).
- **Output binding** — under **Properties → Outputs / Update variables**, **Add new** a **String** case variable **`requester_reply`**, then:
  - **Route A (try first):** if `requester_reply` auto-surfaces as a named task output (Maestro surfaces flat same-named outputs), bind by name — no `hitlTask` parsing.
  - **Route B (fallback path):** set its value via the **tune / `{x}` → Expression** editor to **`hitlTask.data.requester_reply`**. **[CONFIRM-LIVE]** the exact path/casing by browsing `hitlTask` in the expression editor after one completed run.

### 2.3 Branch on `is_vague`
`is_vague` is auto-bound to a same-named case variable from scoping's output.
- **Custodian** task → **Entry rule** **`is_vague == false`**.
- **[CONFIRM-LIVE]** Case Plan branching surface: if your surface uses **Entry rules** (per the Journey-A spike) use the rules above. If it's the **BPMN Exclusive gateway** surface, instead add an **Exclusive gateway** after scoping → **Conditions → Expression editor** → `vars.is_vague == true` → true-line to the clarification task, false-line to custodian. (You built Journey A — use whichever surface that was. The condition is identical either way.)

### 2.4 Re-invoke scoping with the reply
- **Add task → Agent → scoping-agent** (binds by name → latest `2232380`).
- **Inputs:** `text` = **`requester_reply`**; `case_id` = the **`CaseId`** system variable; `jurisdiction` = `"federal_foia"`; `request_id` / `requester` / `submitted_at` from Stage-1's variables; `clarification_round` = `2`.
- **Entry rule:** **`requester_reply != ""`**; **"Run only once"** ON.
- **Outputs → Update variables:** write back to the **same `is_vague`** variable (and `clarification`). Both scoping tasks updating one `is_vague` means the custodian's `is_vague == false` rule fires automatically after round 2 — no merge construct needed.
- **[CONFIRM-LIVE]** a second instance of the same agent in one Case. **Fallback:** use the **Connect tool** to loop a line back to the original scoping task to re-execute it with `text = requester_reply`.

### 2.5 Test Beat 1
Publish → hard-refresh → run the **vague seed** (below) via Debug on cloud → confirm: vague → Action Center task appears → type the narrower request → Submit → second scoping runs → `is_vague=false` → custodian fires. **Beat 1 green.**

### 2.6 Beat-1 fallback (if the typed text won't round-trip in ~15 min)
Drop to an **outcome-only** form (same proven pattern as Beat 2's approve/reject) — the requester still **clicks**, but the narrower request is a **pre-seeded constant**. The click is real; only the prose is canned. The narrative is unchanged (requester clicks → a narrower request flows into the second scoping pass → `is_vague` flips false → custodian fires).
- **5a (simplest):** single **Submit** outcome, **no schema Output property**. The second scoping task's `text` input is a **literal constant** holding the demo's narrower string (the §5 step-3 text). Zero schema-output risk.
- **5b (richer):** 2–3 outcome buttons (e.g. "Narrow to FY2023 contract awards" / "Narrow to grievances"); read the **outcome**, then a small **Script/Business-rule task** maps the chosen outcome → its pre-seeded constant → `requester_reply`. The requester's *choice* is real and visible.

---

## 3. PHASE 2 — Beat 2: officer silent-custodian escalation (~40 min)

### 3.1 Build the Beat-2 App (outcome-only — fast)
- Reuse the working `approve`/`reject` **outcome** pattern; here the outcomes are **"Escalate"** / **"Defer"**.
- **Inputs:** `silent_department` (String) + a context string (case_id / terms).
- No custom field wiring — the chosen **outcome** returns on `hitlTask`.

### 3.2 Wire `record-query-b` into the custodian stage
- After custodian, add **Add task → API workflow → `record-query-b`** (or `Start and wait for API workflow`).
- **Inputs:** `case_id` = **`CaseId`** system var, `jurisdiction` = `"federal_foia"`.
- If it shows **"No inputs configured"**: publish the workflow → hard-refresh → **delete + re-add the task** so it reads the published schema.
- The case reads the returned **`records`** for Review (same as Journey A) and **`silent_department`** for the branch.

### 3.3 Add the escalation User task
- **Add task → Action** → **"Create Action App task"** → Beat-2 app.
- **Entry rule:** **`silent_department != ""`** (gateway alternative: `vars.silent_department != ""`).
- **Inputs:** `silent_department`, context. **Output:** String **`escalation_decision`** = the outcome.
- On completion → proceed to **Review** (thin `2232381`) → **Release** (as Journey A). B's records are clean → Review returns **0 proposals**, no interrupt fires.
- *(Optional, skip unless time):* a non-interrupting reminder timer off CIO=`slow` for the "laddered reminder" visual. Not required for the beat.

### 3.4 Test Beat 2
Publish → hard-refresh → run the B seed → custodian → `record-query-b` reports Communications silent → escalation task appears → officer clicks **Escalate** → proceeds to Review → Release. **Beat 2 green.**

---

## 4. PHASE 3 — regression (5 min)
Re-run the **Journey-A** seed end-to-end. A's record-query is untouched (separate workflow), A is not vague and has no silent dept, so it must stay green. Confirm before recording.

---

## 5. Live demo script (what to say + click on camera)

**Seed — the vague request** (`agents/scoping-agent/fixtures/request_vague.json`):
> "I want all records about the agency's spending and any problems it has had."

1. **Submit** the vague request. Narrate: *"FOIA requires we act on this, but it's too vague to search — no subject, no time bound, no custodian."*
2. The case pauses on the **clarification Action Center task**. Open it as the **requester**. Narrate: *"The Intake agent detected the ambiguity and drafted a narrowing suggestion. The statutory clock tolls while we wait."*
3. As the requester, type the **narrower request** and Submit:
   > "IT modernization program contract awards for calendar year 2023 — records from the Office of Procurement and the Office of the CIO."
4. Narrate: *"On reply, the clock resumes and the agent re-scopes — now searchable."* The case advances to the **custodian fan-out**.
5. The custodian tasks 3 departments; **Office of Communications goes silent**. The case pauses on the **escalation Action Center task**. Open it as the **records officer**. Narrate: *"Procurement responded, the CIO was slow, but Communications never answered — and the clock is still running. The case escalates to a human; it does not stall silently or auto-close."*
6. As the officer, click **Escalate**. Narrate: *"The officer decides — escalate to the custodian's supervisor — and the case keeps full state on the Maestro timeline."*
7. The case proceeds through **Review** (clean records → nothing withheld → full release) and **Release**. Narrate: *"Two responsive records, no exemptions, released in full — the disclosure default."*
8. Show the **Maestro case timeline**: clarification sent, clock tolled/resumed, custodian escalated, released — the full dynamic path on one audit trail.

---

## 6. Gotchas (with the fix)
- **"No inputs configured"** on a workflow task → publish the workflow → hard-refresh (Cmd+Shift+R) → delete + re-add the task.
- **Publish / Debug-on-cloud fails** (`No solution tool factory is registered` / `RPA server connection needs recovery`) → UiPath staging build-service flapping, not your content. Retry every ~20–30 min; turn **"Deploy resources before debugging" OFF**; check status.uipath.com.
- **Stale token mid-session** → Debug fails at *Restore* (`orchestrator unknown`) → hard-refresh / re-log into Automation Cloud, re-run.
- **Untyped array output** → breaks the solution pack. `records` and `statuses` need a typed `Item`.
- **Scoping auto-binds to latest version** (no pinned release). Fine — only `0.0.1` exists.

## 7. Open items to confirm live (none block the architecture)
1. Branching surface — Entry rules vs BPMN Exclusive gateway (§2.3). Use whichever Journey A used.
2. Beat-1 text capture (§2.1) — **mechanism RESOLVED** (In/Out Text property `requester_reply` bound to the control; verified doc-simple, none of the `decisions[]` array friction). Only the literal `hitlTask.data.requester_reply` path is [CONFIRM-LIVE] (Route B) — try Route A (auto-surfaced named output) first; if neither rounds-trips, use **§2.6 Fallback**. Requires the **May 2026 Maestro release** for typed `hitlTask` fields.
3. Two scoping tasks in one Case (§2.4) — fallback is the Connect-tool loop-back.
