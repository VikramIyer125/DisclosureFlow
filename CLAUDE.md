# CLAUDE.md — DisclosureFlow working agreement

DisclosureFlow is a three-agent FOIA case-orchestration system for **UiPath AgentHack, Track 1: Maestro Case**. It must be a *running* solution on UiPath Automation Cloud, not slideware. Positioning, stated everywhere: **the system accelerates compliant disclosure, not denial.** Every withholding requires a human-approved exemption, a source-grounded foreseeable-harm rationale, and an audit-trail entry. The human gate is legally load-bearing, not decorative.

## Source of truth

- **`docs/design-brief.md`** is the source of truth for scope, architecture, the 6-stage lifecycle, the three agents, the four seams, the §8 invariants, data contracts, the three demo journeys, and build order. Read it in full before writing feature code. (If you keep the brief at repo root as `BRIEF.md`, treat that path as equivalent.)
- **`docs/build-prompt.md`** is the operational plan / milestone order.
- Where this file and the brief disagree, the **brief wins** — flag the conflict to me rather than silently resolving it.
- Keep `docs/coding-design.md` (the §8 invariants) and each agent's `AGENTS.md` in sync with the brief.

## Hard rules — never violate (these are the brief's non-negotiables, condensed)

1. **No LangGraph supervisor / orchestrator graph.** Each coded agent is a single-purpose graph that does exactly one stage's reasoning and returns typed JSON. Agents never call each other. All cross-stage routing — stage transitions, branches, fan-out, the clarification toll, silent-custodian escalation, the reject→revise loop — lives in the **Maestro Case model** (Service Tasks, gateways, timers, user tasks, events), not in Python. Tempted to write a graph that sequences the three agents? Stop — that belongs in Maestro.
2. **Closure is never automatic.** The clock never closes a case. Model the federal FOIA response clock as 20 working days with tolling during clarification; support a configurable requester-response grace window (default 30 working days) that routes to a **human** close-out queue. A human always decides to close.
3. **Every redaction grounds in a real PolicyProvider rule** (typed output validation at the agent boundary, brief §8.3) **and** passes the human approval gate. No exemption from agent memory alone.
4. **Only approved bytes are released** (release-integrity guard, §8.4): approval token + hash-check before release, or block.
5. **All side-effecting steps are idempotent** (deterministic keys, check-then-act, upserts, §8.5). This is what makes retries safe.
6. **Corrections memory is advisory, never authoritative** — it informs a proposal, never bypasses the PolicyProvider or the human gate; retrieved corrections surface into the human review.
7. **Build the four seams as injected dependencies** with demo + production backings: PolicyProvider, RecordStore, Clock, CorrectionsMemory. Pass `jurisdiction` as a real parameter from day one (only value: `federal_foia`).
8. **MVP before stretch.** No LlamaIndex retrieval, precedent store, polished auth/RBAC, self-consistency sampling, or portal polish until all three agents deploy and run, the case spine works through Maestro Case, one Action Center approval works, the release-integrity guard works, and Journeys A/B/C are runnable with seeded data.

A change that would break any hard rule is **never** a silent decision — it is an automatic decision gate (see below), and usually a refusal.

## Decision-gate protocol (READ THIS — it governs how you check in with me)

Two outcomes for any non-trivial choice. Pick the right one; when unsure, treat it as a GATE.

**GATE — stop, present analysis, wait for my explicit approval before proceeding.** Use for anything architectural or hard-to-reverse:
- Seam interface signatures (`PolicyProvider`, `RecordStore`, `Clock`, `CorrectionsMemory`) or any change to them.
- Data-contract schema shape or changes (`Request → ScopedRequest → SearchTask[] → CandidateRecord[] → RedactionProposal[] → ApprovedRedaction[] → ReleasePackage`).
- How Maestro invokes agents; the case-model stage/branch structure; HITL **mechanism per gate** (LangGraph interrupt for the redaction gate vs. Maestro User Task for close-out/release — never both for one gate).
- Library/dependency or framework choices that lock the project in.
- Anything that changes the three-journey demo story (A/B/C).
- Any deviation from a brief **hard rule** or **locked default**.
- Repo/package/deployment structure changes.

**LOG — decide the smallest safe demo default, write it to `ASSUMPTIONS.md`, continue.** Use for reversible, cosmetic, or demo-only choices: seed-data values, department names, naming, log/format styling, test-fixture content, portal visuals.

### How to present a GATE

Every gated decision is explained to me in **two frames**, both required:

1. **Competency** — for each viable option: pros, cons, how well it does what it needs to do, and *specifically where it struggles* (edge cases, failure modes, demo risk, platform-availability risk). Don't flatten this into "Option A is better"; show the trade.
2. **Extensibility** — how hard each option is to extend later (new jurisdiction via the PolicyProvider seam, new exemptions, swapping demo→prod backings, adding the corrections/precedent retrieval stretch). What does each option make easy, and what does it foreclose?

End with your recommendation and the one thing you'd want me to weigh. Keep it tight — frames, options, a recommendation — not an essay. Then wait.

Only blocking credentials, tenant access, or irreversible architecture decisions should *halt* work; lower-stakes gates can be batched and raised at the next natural checkpoint rather than interrupting mid-step.

## Subagents (delegate proactively to keep the main context clean)

Hand work to these rather than doing it inline; they exist mainly to isolate noisy context (SDK docs, test code, review diffs) from the main thread. All run on `claude-opus-4-8`.

- **platform-integrator** — anything touching how code reaches or runs on Automation Cloud: `uipath auth/init/pack/publish`, Orchestrator, Maestro Service Task wiring, Action Center, API Workflows, deploy/runtime failure diagnosis. Verifies against current UiPath docs.
- **contracts-seams-architect** — `shared/contracts/` schemas and the four seam Protocols + their demo/prod backings. The backbone everything imports. Invoke before changing any cross-stage data shape (and such changes are GATES).
- **coded-agent-builder** — builds/refines one single-purpose LangGraph coded agent at a time (Intake/Scoping, Custodian/Search, Review & Redaction). Deepest care on the Review hero.
- **invariant-auditor** — read-only reviewer. Run after writing/modifying code and before any commit; checks the diff against §8 invariants and the hard rules. Reports Critical/Warning/Suggestion; never edits.
- **test-engineer** — unit tests for the deterministic pieces against the injected seams.

## Models & effort

- Main session and all subagents: **Opus 4.8** at **high** effort. High is the default; set explicitly with `/effort high` if needed. Effort is a session-level setting — there is no per-subagent effort field, so each subagent pins `model: claude-opus-4-8` in frontmatter and inherits the session's reasoning behavior.
- Model **per build step inside the agents** is a config value (Sonnet 4.6 / Opus 4.8 / Haiku 4.5 per brief §9) — never hardcode model IDs in business logic.

## Working style

- Proceed **incrementally by milestone** (build-prompt order). Milestone 0 — platform capability check and a hello-world deploy round-trip — comes before any FOIA logic. On an unfamiliar platform with a two-week clock, the pipeline is the risk, not the logic.
- Prefer small, testable units. Run the **invariant-auditor** before commits.
- For non-blocking ambiguity: smallest safe demo default → `ASSUMPTIONS.md` → continue. For anything in the GATE list: stop and ask, in the two frames above.
- Deadline: **June 29**. Public GitHub repo + README + ≤5-min demo video (shown running, not slides) + deck are required deliverables.