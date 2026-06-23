# DisclosureFlow — Project Status & Contributor Guide

> **New here? Read these first**, in order:
> 1. [`docs/design-brief.md`](docs/design-brief.md) — **the source of truth** for scope, the 6-stage lifecycle, the three agents, the four seams, the §8 invariants, and the three demo journeys. Read it in full before writing feature code.
> 2. [`CLAUDE.md`](CLAUDE.md) — the working agreement: the **hard rules you must never break** and the **decision-gate protocol** (when to stop and ask vs. log-and-continue).
> 3. [`docs/build-prompt.md`](docs/build-prompt.md) — the milestone plan. 4. [`docs/platform-check.md`](docs/platform-check.md) — the live UiPath tenant state.
>
> This file is the **progress tracker + task board**. It tells you what's done, what's live on the cloud, how to build/run/deploy, and **what to pick up next**.

**What it is:** DisclosureFlow is a three-agent FOIA case-orchestration system for **UiPath AgentHack, Track 1: Maestro Case**. It must be a *running* solution on UiPath Automation Cloud, not slideware. Positioning everywhere: **the system accelerates compliant disclosure, not denial** — every withholding requires a human-approved exemption, a source-grounded foreseeable-harm rationale, and an audit-trail entry. The human gate is legally load-bearing.

**Deadline:** June 29. Required deliverables: public GitHub repo + README + ≤5-min demo video (shown *running*, not slides) + deck.

---

## Status at a glance

| Milestone | Scope | Status |
|---|---|---|
| **0 — platform check** | Verify Maestro Case, coded-agent deploy, Action Center, API Workflows; hello-world round-trip | ✅ **Done** (`docs/platform-check.md`, all blocking rows PASS) |
| **1 — three agents** | Contracts + 4 seams; Intake/Scoping, Custodian/Search, Review & Redaction — each deploys & runs | ✅ **Done** — all three **live on the cloud** |
| **2 — case spine** | Wire the agents through Maestro Case; record-query + release steps; ≥1 Action Center approval | 🔶 **In progress** — groundwork + HITL gate done; Maestro authoring + API Workflows + Action Center app remain |
| **3 — three journeys** | A (fast-track), B (clarification/tolling/silence), C (redaction governance) on seeded data | ⬜ Not started |
| **4 — safeguards** | Typed validation, idempotency, release-integrity guard, audit timeline | 🔶 Built in code; needs Maestro wiring |
| **5 — polish** | Portal, corrections-memory retrieval, self-consistency, DU, Drive, **video + deck** | ⬜ Not started |

---

## What's built (and verified)

### The shared backbone — `shared/` (Milestone 1)
One importable package every agent + the portal + the case model consume. **Do not redefine these contracts in an agent — import them.**
- `shared/contracts/` — the §10 pipeline in **Pydantic v2**: `Request → ScopedRequest → SearchTask[] → CandidateRecord[] → RedactionProposal[] → ApprovedRedaction[] → ReleasePackage`, plus supporting types (`Span`, `RecordContext`, `Correction`, `SearchTerms`, `QueryResult`, `ExemptionTestResult`, `ConfidenceSignal`, `ClarificationDraft`). Also: `derive_confidence()` (§8.1, confidence is **derived, never asked of the model**), the §8.3 boundary validators (`validate_proposal`/`validate_test_completeness`), and the §8.5 `idempotency_key` helpers. Security-critical fields (`rule_id`, hashes, tokens) are Strict-typed.
- `shared/seams/` — the four swappable seams as Protocols with demo + prod backings: `PolicyProvider` (`FederalFoiaPackProvider`), `RecordStore` (`LocalFolderRecordStore`), `Clock` (`ManualClock`), `CorrectionsMemory` (`AppendOnlyCorrectionLog`). Prod backings are honest `NotImplementedError` stubs.
- `shared/release/` — `integrity.py` (the §8.4 release-integrity guard: only approved bytes leave; returns a *block result*, never leaks) and `mask.py` (the single source of truth for the `█` length-preserving redaction mask + content hash, imported by both the Review agent and the release step so the §8.4 chain cannot diverge).
- `policy-packs/federal-foia/pack.json` — the versioned PolicyProvider pack: exemptions **b5** (deliberative), **b6** & **b7c** (balancing). Each rule declares its `required_test_elements`, so adding an exemption is a pack edit, not a code change.

### The three coded agents — `agents/` (Milestone 1)
Each is its **own independently-packageable UiPath project**, a single-purpose LangGraph graph that does **exactly one stage** and returns typed JSON. **Agents never call each other** — all cross-stage routing lives in Maestro.
| Agent | Stage | Model | Output | Live release |
|---|---|---|---|---|
| `scoping-agent` | Intake/Scoping | Sonnet 4.6 | `ScopedRequest` (+ optional `clarification`) | **2232380** |
| `custodian-search-agent` | Search tasking | Opus 4.8 | `SearchTask[]` (in a `SearchPlan` envelope) | **2232377** |
| `review-redaction-agent` | Review & Redaction (**the hero**) | Opus 4.8 | `ApprovedRedaction[]` after the HITL gate | **2232381** (thin; HITL version built, **awaiting re-deploy**) |

The **Review hero** does responsiveness review + exemption classification (5/6/7(C)) + source-grounded foreseeable-harm rationale + structured `test_result` + **derived** confidence, §8.3-validated at the boundary. It now also implements the **§5 redaction-approval HITL gate** (a LangGraph `interrupt → Action Center task → resume`, with a bounded reject→revise loop), emitting `ApprovedRedaction[]` whose hashes are byte-identical to what the release guard recomputes. All three agents are **audited PASS** against the §8 invariants and verified running on the cloud.

### Milestone-2 groundwork
- `steps/` — the two mechanical steps as deterministic Python behind the seams (the "Python-first" stage before they become API Workflows): `record_query_step.py` (`SearchTask → QueryResult`, `content_hash`, silent-vs-legitimate-negative, `query_key` dedupe) and `release_step.py` (assemble `ReleasePackage`, apply the §8.4 guard, Bates, idempotent on `package_id`).
- `demo-data/` — seeded department folders for journeys A/B/C with per-department behavior (responded/slow/silent). Journey-A/C records are **byte-identical to the agent fixtures**, so `content_hash` reproduces end-to-end.
- `tests/` — **18 deterministic tests** (four query behaviors, three release-block cases, idempotency). All pass: `uv run python -m pytest tests/`.
- `maestro/case-model-spec.md` — the proposed 6-stage Maestro spine mapped to concrete constructs. **The 4 gate decisions in it are APPROVED** (adopt as written).

---

## Live platform state (UiPath Automation Cloud — staging)

You need access to the **staging** tenant `hackathon26_632` / `DefaultTenant` to deploy/invoke. See `docs/platform-check.md` for the full capability matrix.

- **Everything lives in shared Orchestrator folder `Shared`** (id `3083529`, key `257dab65-2353-4e0c-96e8-ff9f3746d9ed`), which has a **Default Serverless** runtime assigned (jobs run, not Pending).
- **Published processes:** the three agents above (releases 2232380 / 2232377 / 2232381).
- **Asset:** `DisclosureFlow_AnthropicApiKey` (Text asset) shared into the folder — the agents read it on the robot via the SDK secret-asset fallback (no Orchestrator UI env binding needed).
- The Anthropic API key is read **env-first then SDK-asset-fallback** (`_resolve_anthropic_key`); model id is per-step config, never hardcoded.

### Platform gotchas (learned the hard way — save yourself the pain)
- **Build via the Makefile, always.** `make pack AGENT=<name>` vendors `shared/` (and `policy-packs/` for the Review agent) into the agent dir before packing. A bare `uipath pack` silently omits them → ImportError on the robot.
- **Opus 4.8 rejects the `temperature` param** — omit it (the config does, unless explicitly set).
- **`uipath pack` rejects `&`** in the project description.
- **Non-`.py` data files** (e.g. the policy-pack `.json`) need `packOptions.fileExtensionsIncluded: [".json"]` in `uipath.json` or they're dropped from the nupkg.
- **Invoke via the SDK/httpx transport, not raw `curl`** — raw `StartJobs` is WAF-blocked (code 1010). Use header `x-uipath-folderkey: <folder key>`.
- **Auth token is ~1h-lived.** Re-`uipath auth --staging` (or refresh) before long publish/invoke sessions.
- **`uipath run` loads the *agent dir's* `.env`** (which is empty); for a live local run, source the root `.env` first.

---

## How to build, run, test, deploy

```bash
# One-time: Python env (uv) is at repo root (.venv); the agents have their own .venv too.
# Secrets live in the repo-root .env (ANTHROPIC_API_KEY, UIPATH_*). Not committed.

# --- Run the deterministic tests (no cloud, no LLM) ---
uv run python -m pytest tests/ -q

# --- Run an agent locally (live LLM) ---
cd agents/review-redaction-agent
set -a && . ../../.env && set +a            # load keys into the run env
uv run uipath run agent --file fixtures/records_exemption_heavy.json

# --- Build + deploy an agent to the Shared folder (from repo root) ---
make pack    AGENT=review-redaction-agent   # vendor shared/ (+policy-packs) → init → pack
make publish AGENT=review-redaction-agent   # ... → publish to Orchestrator
```

Each agent has its own `AGENTS.md` documenting its design, invariants, and fixtures — read it before touching that agent.

---

## The hard rules (never break these — full text in `CLAUDE.md` & brief §8)

1. **No LangGraph supervisor / orchestrator graph.** Each agent is one single-purpose graph returning typed JSON. **Agents never call each other.** All cross-stage routing — stages, branches, fan-out, the clarification toll, custodian escalation, the reject→revise loop — lives in the **Maestro Case model**, not Python. (The one exception, explicitly authorized by brief §5: the redaction-approval interrupt + revise loop live *inside* the Review agent.)
2. **Closure is never automatic.** The clock never closes a case; a human always decides close-out. 20-working-day FOIA clock with tolling; configurable 30-working-day grace window routes to a human queue.
3. **Every redaction grounds in a real PolicyProvider rule** (typed validation at the agent boundary, §8.3) **and** passes the human approval gate. No exemption from agent memory.
4. **Only approved bytes are released** (§8.4: approval token + hash-check before release, or block).
5. **All side-effecting steps are idempotent** (deterministic keys; §8.5).
6. **Corrections memory is advisory, never authoritative.**
7. **The four seams are injected dependencies** with demo + prod backings; `jurisdiction` is a real parameter from day one.
8. **MVP before stretch** (no LlamaIndex/precedent/self-consistency/portal-polish until all three journeys run).

### Decision-gate protocol (how to check in)
- **GATE** (stop and ask before proceeding): any change to a **contract schema** or **seam signature**, how Maestro invokes agents, the **case-model structure**, the **HITL mechanism per gate**, framework/dependency choices, the three-journey demo story, repo/deploy structure, or any deviation from a hard rule. Present the choice in **two frames — competency + extensibility** — with a recommendation, then wait.
- **LOG** (decide the smallest safe demo default, write it to `ASSUMPTIONS.md`, continue): reversible/cosmetic/demo-only choices (seed values, naming, fixtures, styling).
- `ASSUMPTIONS.md` (gitignored, local) is the running ledger of LOG-tier decisions — read it for context on why things are the way they are.

### Subagents (if you use Claude Code)
`.claude/agents/` defines specialized subagents — delegate to keep context clean: **platform-integrator** (anything touching the cloud/deploy), **contracts-seams-architect** (`shared/` changes — a GATE), **coded-agent-builder** (one agent at a time), **invariant-auditor** (read-only §8 review before every commit), **test-engineer** (unit tests). Run the **invariant-auditor before any commit** that touches agent boundaries, side-effecting actions, the release path, the clock, or the seams.

---

## What needs to be done — task board

Priority: **P0** = on the critical path to a running demo; **P1** = needed for full journeys; **P2** = stretch/polish. Type: **[SW]** authored in Studio Web (not Python), **[platform]** UiPath CLI/SDK/Orchestrator, **[coded]** Python in this repo, **[deliverable]** assets.

### Milestone 2 — the case spine (current focus)
- **P0 [SW] Author the Maestro Case model** — the 6-stage spine per `maestro/case-model-spec.md`. **Start with a thin spike** (one stage + one "Start and wait for agent" Service Task + one timer) to confirm the real Studio Web construct names, then build out. The agents are the three Service Task targets (releases above). *This is the Track-1 requirement — the whole thing must run as a Maestro Case.*
- **P0 [SW] Author the two API Workflows** — `record-query` (wraps the logic in `steps/record_query_step.py` / `RecordStore.query`) and `release` (wraps `steps/release_step.py` + the §8.4 guard). The Python steps already encode the behavior; the API Workflows are the platform-native version wired as Service Tasks.
- **P0 [platform] Action Center action app + HITL Review deploy** — create the `DisclosureFlow_RedactionReview` action app the Review agent's `interrupt` targets; re-deploy the HITL Review version (replacing release 2232381); prove the full **interrupt → Action Center task → resume** round-trip on the cloud. *Can start now; independent of the Maestro authoring.* (platform-integrator)
- **P0 [SW] Wire the spine** — agents as Service Tasks with data mapped between stages per the contracts; inject `available_departments` (from `RecordStore.list_departments`) before the custodian agent; map `CandidateRecord[]` from record-query into Review.
- **P1 [SW] Clock & exceptions in Maestro** — the 20-day clock + tolling + 30-day grace timer → human close-out; the branches: vague→clarification (clock tolls), silent/slow custodian (off `QueryResult.status`) → reminder → escalation. (The reject→revise loop is already handled inside the Review agent.)

### Milestone 3 — the three journeys (on seeded data)
- **P0** Journey **A** (fast-track) end-to-end — proves the spine.
- **P1** Journey **B** (clarification + tolling + custodian silence → escalation) — proves dynamic case management.
- **P1** Journey **C** (redaction governance: HITL approve/reject/revise + release-integrity block before approval) — proves legal accountability. *Depends on the Action Center app.*

### Milestone 4 — safeguards (mostly built; wire them)
- **P1 [SW]** Wire the §8.4 release-integrity guard into the release API Workflow (the guard fn exists in `shared/release/integrity.py`).
- **P2 [SW]** Confirm Maestro's native audit timeline covers the §6 audit requirement (lean on it, don't rebuild).

### Milestone 5 — polish & deliverables
- **P1 [coded] Requester portal** (`portal/`, Python) — submit a request, view status (stage only), respond to a clarification, download the released package. The only UI built from scratch.
- **P0 [deliverable]** ≤5-min demo **video** (shown running), **deck**, and a polished **README** (problem, architecture, run instructions, demo link).
- **P2 [coded]** Corrections-memory retrieval via LlamaIndex (advisory; never bypasses PolicyProvider/human gate).
- **P2 [coded]** Self-consistency sampling in the Review agent (§8.1c — the `derive_confidence` third param is already wired).
- **P2 [SW/coded]** Google Drive `RecordStore` backing + record-query as a Drive-backed API Workflow (demo polish — local folders work fine for now).
- **P2 [coded]** Document Understanding on intake (fallback is the seeded-JSON parser — don't let DU block anything).

### Backlog / tech debt
- **P1 [coded] — good first issue** Permanent pytest coverage for the HITL gate (the scenarios were verified via a since-deleted scratch script; convert them to proper `tests/` without module-scope monkeypatching). (test-engineer)
- **P2 [coded]** LangGraph msgpack checkpoint hygiene — the persisted state types warn "blocked in a future version"; register/allowlist them.
- **P3 [coded] — good first issue** De-duplicate edited-span resolution between `steps/release_step.py` and `shared/release/mask.py`.

---

## Commit history (this branch: `feature/contracts-seams-backbone`)
```
22cf17e  Add redaction-approval HITL gate to Review agent (§5)
6dfb7c2  Add Milestone 2 groundwork: seam steps, seed data, case-model spec
11a7025  Fix review agent description: uipath pack rejects '&'
22ba78a  Add thin Review & Redaction hero agent (Milestone 1)
04c8f0c  Add thin Custodian/Search coded agent (Milestone 1)
dbaf774  Add thin Intake/Scoping coded agent (Milestone 1)
15607eb  Add contracts + seams backbone (Milestone 1)
```
