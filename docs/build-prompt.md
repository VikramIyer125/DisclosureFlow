Claude Code build prompt — FOIA Case Management (UiPath AgentHack, Track 1: Maestro Case)
Paste this into Claude Code at the root of the (empty) repo. The full design lives in docs/design-brief.md — read it first and treat it as the source of truth. This prompt is the operational plan.

You are building DisclosureFlow, a three-agent FOIA case-orchestration system for UiPath AgentHack Track 1: Maestro Case. It must be a running solution on UiPath Automation Cloud, not a slideware prototype. The system uses three specialized LangGraph coded agents — Intake/Scoping, Custodian/Search, and Review & Redaction — orchestrated by Maestro Case with HITL through Action Center.
The guiding principle: DisclosureFlow accelerates compliant disclosure, not denial. Every withholding must be grounded in a real PolicyProvider rule, include a source-grounded rationale and foreseeable-harm explanation, pass through human approval, and appear in the audit timeline.
Before writing feature code, read docs/design-brief.md in full and treat it as the source of truth. Ask me only for blocking credentials, tenant access, or irreversible architecture decisions. For product/design ambiguity, choose the smallest safe demo default, document it in ASSUMPTIONS.md, and continue.
Hard rules — never violate these
Runs on UiPath Automation Cloud. Agents are LangGraph coded agents built with the UiPath Python SDK, packaged with the UiPath CLI, deployed to Orchestrator, invoked by Maestro Case as Service Tasks ("Start and wait for agent"), with HITL via Action Center. Python only. 1a. No LangGraph supervisor / orchestrator graph. Forbidden. Each coded agent is a single-purpose graph that performs exactly one stage's reasoning and returns typed JSON. Agents never call each other. All cross-stage routing — stage transitions, branches, fan-out, the clarification toll, the silent-custodian escalation, the reject→revise loop — lives in the Maestro Case model (Service Tasks, gateways, timers, user tasks, event triggers), not in Python. If you are tempted to write a graph that sequences the three agents, stop: that logic belongs in Maestro.
Closure is never automatic. The clock never closes a case. Model the federal FOIA response clock as 20 working days with tolling during clarification. Separately, support a configurable requester-response grace window, default 30 working days, after which a timer routes the case to a human close-out queue. A human always decides whether to close.
Every redaction grounds in a real PolicyProvider rule (typed output validation at the agent boundary — see brief §8.3) and passes through the human approval gate. No exemption from agent memory alone.
Only approved bytes can be released (release-integrity guard — brief §8.4): approval token + hash-check before release, or block.
All side-effecting steps are idempotent (deterministic keys, check-then-act, upserts — brief §8.5). This is what makes retries safe.
Build the four seams as injected dependencies with demo + production backings: PolicyProvider, RecordStore, Clock, CorrectionsMemory (brief §7). Pass jurisdiction as a real parameter from day one.
Corrections memory is advisory, never authoritative — it informs the proposal, never bypasses the PolicyProvider or the human gate, and retrieved corrections are surfaced into the human review.
MVP before stretch. Do not implement LlamaIndex retrieval, precedent store, polished auth/RBAC, self-consistency sampling, or advanced portal polish until:
all three coded agents deploy and run,
the case spine works through Maestro Case,
at least one Action Center approval works,
the release-integrity guard works,
Journey A, Journey B, and Journey C are runnable with seeded data.
Build order — do Milestone 0 before anything else
Milestone 0 — platform capability check
Verify access to UiPath Automation Cloud, Orchestrator, coded-agent deployment, Maestro Case, Action Center, API Workflows, and Document Understanding. Record results in docs/platform-check.md.
Then smoke-test the deploy pipeline with one trivial hello-world LangGraph coded agent: run uipath auth / uipath init / uipath pack / uipath publish, confirm it runs on Automation Cloud, and confirm it appears in Orchestrator. Do not build FOIA logic until this round-trips.
Scaffold the monorepo (brief §12) and shared/contracts (brief §10).
Build the four seams + MVP backings (brief §7): PolicyProvider with the versioned federal-foia pack (exemptions 5/6/7(C)); RecordStore over department folders with a per-department behavior config (respond/slow/silent/wrong_docs); controllable Clock (manual advance); CorrectionsMemory/log as an append-only correction log. Do not build LlamaIndex retrieval until after the three core journeys run.
Build the three agents (brief §3), wiring in the failure-plane invariants (brief §8). All three agents must first exist as thin, independently runnable coded agents before advanced reasoning is added:
Intake/Scoping — scope, track, and the agentic clarification loop (draft narrowing suggestion; never silently narrow).
Review & Redaction — responsiveness + exemptions + foreseeable-harm rationale; confidence via structured-test completeness + deterministic risk. Self-consistency sampling is allowed only after the basic Review agent runs.
Custodian/Search — departments + search terms + fan-out.
If a model is unavailable, fall back to the configured default/provider config and document the change in ASSUMPTIONS.md.
Robots/DU (brief §4): default to the seeded-JSON fixture / deterministic parser for intake extraction so DU never blocks the case demo. Treat Document Understanding as a Milestone-5 polish item: integrate it on intake ONLY if it is confirmed available and you have time after the three journeys run. Do not spend Milestone-0/1 time getting DU working. Record-store query and release/production are UiPath API Workflows authored in Studio Web / Integration Service — NOT Python. Do not write Python that simulates an API Workflow. Build order: first implement record-store query and release as deterministic Python tools behind the RecordStore seam and the release step so the journeys run end-to-end; THEN, once API Workflow access is confirmed (Milestone 0, item 5), re-implement these two steps as real API Workflows for platform-usage credit and wire them as Maestro Service Tasks. Record any export of the API Workflow definitions under workflows/ as reference artifacts.
7. HITL via Action Center (brief §5), split deliberately by mechanism:
Redaction-approval gate → a LangGraph interrupt inside the Review agent, because the officer's accept/reject/edit must re-enter the agent to drive the revision loop. The interrupt surfaces an Action Center task; the agent resumes on the decision.
Close-out decision and final release approval → Maestro User Tasks (Action Center), owned by the case model, because these are case-level governance gates with no feedback-into-reasoning requirement. Do not implement both mechanisms for the same gate.
Wire the lifecycle in Maestro Case (brief §6): case manager + stage manager agents, statutory clock with tolling, audit log. Lean on Maestro's native pause/resume and audit rather than rebuilding.
Requester portal in Python (brief §11): submit request, view status (stage only), respond to clarification, download package — a thin client calling UiPath APIs.
Demo data + optional pre-seeded correction-log entries; make the three journeys runnable (brief §11). Build at least one visible governance/interruption beat, preferably release-integrity blocking before approval or custodian silence escalating to a human/task queue.
Testing
Unit-test the deterministic pieces against the injected seams: PolicyProvider lookups, typed output validation, idempotency keys, 20-working-day clock/tolling math, requester-response grace window, human-only close-out routing, and the release-integrity guard. Self-consistency sampling runs only on the Review agent and is stretch after the basic Review agent runs.
Stub, don't build
Multi-jurisdiction (architected via the PolicyProvider seam only), RBAC/security hardening, fees, consultations, video redaction, rolling releases, PII-at-rest, litigation. Appeals are an optional stretch.
Working style
Proceed incrementally by milestone. Write docs/coding-design.md for the §8 invariants and keep the per-agent AGENTS.md files in sync with the brief. Prefer small, testable units. Ask only for blocking credentials, tenant access, or irreversible architecture decisions. For non-blocking ambiguity, choose the smallest safe demo default, document it in ASSUMPTIONS.md, and continue.

