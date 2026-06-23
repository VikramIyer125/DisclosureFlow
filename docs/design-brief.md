DisclosureFlow — FOIA Case Orchestration Design Brief
Project: Agentic case-management system for processing public-records (FOIA) requests end to end. Hackathon: UiPath AgentHack — Track 1: Maestro Case. Positioning (say this everywhere): the system accelerates compliant release, not denial. Every withholding requires a human-approved exemption and a drafted foreseeable-harm rationale, and every action is logged to an audit timeline. The human gate is legally load-bearing, not decorative.
DisclosureFlow is a Maestro Case system for federal FOIA request processing. It uses three specialized coded agents — Intake/Scoping, Custodian/Search, and Review & Redaction — to coordinate dynamic FOIA cases across ambiguity, custodian response, exemption risk, human review, statutory-clock state, and release approval. The system accelerates compliant disclosure, not denial. Every withholding requires a human-approved exemption, source-grounded rationale, foreseeable-harm explanation, and audit trail. The human gate is legally load-bearing, not decorative.
This document is the source of truth. The Claude Code build prompt references it; the eventual AGENTS.md should be derived from it.

1. Scope
MVP In Scope:
A single fictional federal agency; federal FOIA-style workflow only.
Three specialized coded agents:
Intake/Scoping Agent
Custodian/Search Agent
Review & Redaction Agent
Three demo journeys:
Journey A: low-complexity fast-track
Journey B: clarification + custodian silence
Journey C: redaction governance
Exemptions 5, 6, and 7(C) (the workhorses; 6 and 7(C) require a balancing test, 5 covers deliberative-process).
The 6-stage case lifecycle (below).
One deterministic PolicyProvider (non-LLM rule source; returns allowed FOIA rules so agents cannot invent exemptions or citations).
One demo RecordStore with 2–3 departments (synthetic department folders with configurable behavior: respond, slow, silent, wrong_docs).
One controllable Clock (injected deadline service; models the 20-working-day FOIA clock, tolling, deadline risk, and demo time jumps).
Human approval through Action Center (pauses the case for records-officer decisions on redactions, close-out, and release).
Release-integrity guard (blocks release unless the exact approved artifact has a human approval token and matching hash).
Agentic clarification loop (agent detects vague scope and drafts a narrowing suggestion to the requester).
Multi-custodian search with exception handling (silent / slow / wrong-docs custodians).
AI-proposed redactions with human approval of every release.
A statutory clock with tolling and a human-only close-out (the clock never closes a case).
An append-only corrections log for officer corrections; retrieval/vector memory is stretch.
A full audit timeline.
Out (stub the seam or omit entirely — do not build):
State/local public-records laws and multi-jurisdiction → architected for via the PolicyProvider seam, not built.
Video/audio redaction; fee categories and waivers; inter-agency consultations/referrals; rolling releases; full RBAC and security hardening; PII-at-rest protection; litigation.
Appeals → optional stretch only.
Locked defaults: requester portal in Python; exemptions 5/6/7(C); append-only correction log for MVP; vector retrieval/precedent store as stretch; two stubbed identities (requester, records officer) with no real auth; manual-advance demo clock.
Stretch Goals after core journeys are run:
Corrections memory retrieval via LlamaIndex (retrieves past officer corrections as advisory guidance; never bypasses PolicyProvider or human approval).
Precedent store (retrieves prior released decisions/response letters as drafting context; advisory only, not authoritative).


2. The case lifecycle (6 stages)
#
Stage
Primary owner
1
Intake & perfection
Intake/Scoping agent + Document Understanding or fallback parser
2
Triage & track
Intake/Scoping agent (light)
3
Search & custodian tasking
Custodian/Search agent + record-store query (API Workflow)
4
Review & redaction proposal
Review & Redaction agent
5
Human review gate
Records officer (Action Center)
6
Release & production
Release/production (API Workflow)

Track 1 principle: the lifecycle stages are stable, but the path through them is dynamic. Each journey creates a different case path based on case facts:
Journey A proves fast-track completion.
Journey B proves ambiguity handling, clock tolling, custodian fan-out, silence, reminder, and escalation.
Journey C proves legal-risk routing, human redaction approval, revision, and release integrity.
This is not a fixed BPMN sequence; Maestro Case coordinates different case paths as the work unfolds.
Exception branches off this spine:
Vague scope at stage 1 → agentic clarification loop (clock tolls; on no reply → human close-out decision, never auto-close).
Silent/slow/wrong custodian at stage 3 → reminder → escalation.
Officer rejects a redaction at stage 5 → agent revises → re-review.

3. Agents (LangGraph coded agents, UiPath Python SDK)
Intake/Scoping Agent — Sonnet 4.6 (claude-sonnet-4-6) Interprets the request, detects ambiguity, proposes scope + track. When scope is too vague to search, drafts a requester-facing narrowing suggestion (the clarification loop) and re-scopes on reply. Suggests narrowing as optional ("I can fill this narrower version faster — or keep your original"); never silently narrows.
Review & Redaction Agent — Opus 4.8 (claude-opus-4-8) (the hero) Responsiveness review, exemption classification (5/6/7(C)), and a drafted foreseeable-harm rationale per redaction. Emits a confidence signal (see §8.1).High-confidence → light batch approval; low-confidence → close scrutiny + corrections-log lookup if available. This is the only agent that runs self-consistency sampling. Default posture is disclosure: the agent must justify every withholding against a specific PolicyProvider rule with a source-grounded, foreseeable-harm rationale, and a human must approve it — the burden is on withholding, mirroring FOIA's presumption of openness. The agent never withholds by default and never withholds on its own authority.
Custodian/Search Agent — Opus 4.8 (claude-opus-4-8) Picks which departments to task and generates search terms; drives the fan-out across department repositories.
Light/fast steps (triage/track classification, drafting the clarification message text, field extraction/normalization) run on Haiku 4.5 (claude-haiku-4-5-20251001). Model is a per-step config value so any step can be dialed up or down without touching logic.

4. Robots / Document Understanding
Intake extraction — use UiPath Document Understanding if available and working in the tenant. MVP fallback is a deterministic parser or seeded JSON fixture so DU does not block the case demo.
Record-store query — UiPath API Workflow (API-first, deterministic, system-to-system): fans out to department repositories and returns candidate records.
Release/production — UiPath API Workflow: applies the approved redactions (non-removable), Bates-numbers, assembles the package. Subject to the release-integrity guard (§8.4).
(Implementing the two mechanical steps as real API Workflows is deliberate — it shows the platform doing deterministic integration work distinct from the reasoning agents, which is scored under Platform Usage.)

5. Human-in-the-loop (Action Center, via LangGraph interrupts)
Ownership by gate: the redaction-approval gate is a LangGraph interrupt inside the Review agent (feedback drives the revise loop); the close-out and final-release gates are Maestro User Tasks owned by the case model. Use the right mechanism per gate; never both for one gate.
Redaction approval gate (mandatory, non-negotiable) — officer accepts/rejects each proposed redaction. Low-risk/high-confidence proposals may be grouped for easier review, but Exemptions 6 and 7(C) always receive full human review because they require balancing. Rejections/additions feed the corrections log or corrections memory if implemented.
Close-out decision (mandatory) — when the clarification window lapses, the timer routes the case to a human queue; a human decides whether to close. The clock never auto-closes. Configurable requester-response grace window, default 30 working days. This is separate from the federal FOIA response clock, modeled as 20 working days with tolling for clarification. The clock never auto-closes a case; it routes close-out to a human.
Clarification send-off (optional, lightweight) — because sending tolls the clock, the agent-drafted narrowing message can require a one-click human "send," or run autonomous-but-logged.
LangGraph's native interrupt pattern maps to an Action Center task: execution pauses, a task is created for the officer, execution resumes on their decision.

6. Orchestration (Maestro Case)
Maestro Case is the backbone (and the Track 1 requirement). It treats the case as a living entity carrying its data, participants, and timeline. Use Maestro Case's native case-manager / stage-manager constructs to govern the lifecycle and drive each stage. These are Maestro modeling concepts expressed in the case model — not additional coded agents to build. The three coded agents are invoked from the case model as Service Tasks; the case model itself owns sequencing, branching, timers, and stage state. Maestro provides much of the audit trail, long-running pause/resume, and exception routing — lean on it rather than rebuilding. It holds the statutory clock, the audit log, and the close-out timer (which routes to a human, never closes).

7. Swappable interfaces — the seam pattern
Build all four as injected dependencies behind interfaces, each with a demo backing and a production backing. This is what makes the system testable, demo-controllable, and extensible. Pass jurisdiction as a real parameter from day one even though the only value is federal_foia.
PolicyProvider — deterministic legal rules (NOT a vector store).
class PolicyProvider(Protocol):
    def get_applicable_rules(self, jurisdiction: str, record_type: str) -> list[Rule]: ...

@dataclass
class Rule:
    id: str                  # e.g. "b6"
    citation: str            # "5 U.S.C. § 552(b)(6)"
    text: str
    test: Literal["categorical", "balancing", "foreseeable_harm"]
    foreseeable_harm: bool
# Pack metadata: pack_id, version, effective_date. Demo pack = "federal-foia".
# The agent reasons over WHATEVER comes back — never hardcodes rule IDs or counts.

RecordStore / CustodianRepository — N separate department repositories with a controllable behavior layer.
class RecordStore(Protocol):
    def list_departments(self) -> list[str]: ...
    def query(self, department: str, terms: SearchTerms) -> list[CandidateRecord]: ...
# Per-department behavior config for the demo: respond | slow | silent | wrong_docs
# Demo backing = Google Drive folders-per-department; dev backing = local folders.
# The realism that matters is the fan-out + controllable response, not the medium.

Clock — injected everywhere a deadline is computed; never read wall-clock directly.
class Clock(Protocol):
    def now(self) -> datetime: ...
    def advance(self, business_days: int) -> None: ...   # demo only (manual advance)
# Production = real clock. Demo = controllable clock (manual advance preferred for
# deterministic recording). Supports the 20-working-day FOIA response clock, tolling during clarification, deadline-risk status, and a separate configurable requester-response grace window.

CorrectionsMemory — optional demo enhancement after core journeys run.
MVP backing = append-only correction log. Store each officer correction with grounding, not just "rejected."
Retrieval/vector backing = optional stretch using LlamaIndex.
class CorrectionsMemory(Protocol):
    def add(self, c: Correction) -> None: ...
    def retrieve(self, ctx: RecordContext, k: int) -> list[Correction]: ...

@dataclass
class Correction:
    direction: Literal["over_redaction_rejected", "missed_redaction_added"]
    record_context: RecordContext   # type + snippet/embedding
    rule_id: str
    rationale: str
    span: Span
    jurisdiction: str
    pack_version: str
    officer: str
    timestamp: datetime
# Corrections are advisory, never authoritative — they inform the proposal, never bypass PolicyProvider or the human gate.
# If retrieval is implemented, surface retrieved corrections into the human review.
# Optional precedent store = stretch only, using the same LlamaIndex pattern over released-FOIA precedent.

(Optional precedent store = same LlamaIndex pattern over released-FOIA precedent.)

8. Failure-plane invariants (non-negotiable engineering rules)
These are platform-agnostic and must be built in from the start, not retrofitted.
MVP-critical invariants:
Typed output validation.
Release-integrity guard.
Idempotency for side-effecting actions.
Demo-enhancing invariants:
Self-consistency sampling.
Corrections-memory lookup.
Full retry/dead-letter framework.
8.1 Confidence-based routing.Do not ask the model for a confidence number. Derive it from, in priority order: (a) deterministic rules — balancing-test exemptions (6, 7(C)) ALWAYS go to full human review, regardless of any other signal; (b) structured-test completeness — the agent must fill each element of the legal test with evidence; any hedged or blank element = low confidence; (c) self-consistency (stretch) — once the basic Review agent runs, sample its exemption classification 3–5× and treat disagreement as low confidence. (a) and (b) are MVP and cheap; (c) is a refinement and consumes 3–5× the Opus calls.
8.2 Step failure policy. Each step classifies its failure: transient (timeout, rate limit) → retry with capped exponential backoff; permanent (malformed output, auth) → no retry; legitimate-negative ("no records," "no exemption applies") → flow forward, NOT a failure. Unrecoverable → defined fallback, then dead-letter to a human queue with full context. The case pauses and keeps state, never dies. Timeouts trip the policy. Policy is per-step config.
8.3 Typed output validation (at the agent boundary). The instant the Review agent returns, validate: rule_id ∈ the set the PolicyProvider returned for this case; the required test fields are populated; the record_ref is in scope. Fail → re-prompt with the specific violation, or route to human flagged. This enforces the "every exemption grounded in a real rule" invariant rather than hoping for it.
8.4 Release-integrity guard. The release step consumes only artifacts carrying an approval token tied to the specific human approval, and hash-checks the bytes about to be released against the approved version. Missing token or hash mismatch → block the release. Only approved bytes can ever leave the system.
8.5 Idempotency. Every side-effecting action carries a deterministic key (e.g. case123:clarification:round1); downstream dedupes on it. Check-then-act before side effects; upserts not appends. Lean on Maestro's durable execution for replay; key/guard your own external side effects (mail, file write, portal submit). Only external-side-effect steps need this. Idempotency is the precondition that makes retry (8.2) safe.

9. Models per step
Step
Model
Intake/Scoping — interpret, detect vagueness, scope
Sonnet 4.6
Review & Redaction — responsiveness, exemptions, harm rationale
Opus 4.8 (self-consistency 3–5×)
Custodian/Search — departments + search terms
Opus 4.8
Triage/track classification
Haiku 4.5
Clarification message drafting
Haiku 4.5
Field extraction / normalization
Haiku 4.5
Embeddings (corrections + precedent)
embedding model

Model is a per-step config value.

10. Data contracts (shared/contracts/)
One source of truth for the schemas that pass between stages: Request → ScopedRequest (track, extracted fields) → SearchTask[] → CandidateRecord[] → RedactionProposal[] (each with rule_id, citation, rationale, confidence) → ApprovedRedaction[] → ReleasePackage. The portal and the agents both consume these. If the portal shares the agents' language (Python), import the models directly; otherwise define them once as schema and validate against them.

11. Demo plan
Two views.
Requester portal (custom, Python): submit a request, see status (stage only), respond to a clarification, download the released package. The only thing that needs building from scratch.
Employee view: lean on native UiPath surfaces — Action Center for pending approvals + Maestro Process Apps / case view for stages, timeline, and decisions. Showing these running is showing platform usage.
Narrative ≤5 min: show three short case journeys inside the same Maestro Case system. Do not present these as three unrelated demos; present them as three case instances governed by the same case architecture.

 Journey A — Low-complexity fast-track:
Clean request → Intake/Scoping Agent extracts scope → Custodian/Search Agent finds one responsive record → Review & Redaction Agent finds no exemptions → human release approval → package generated.
Purpose: proves the end-to-end spine.
Journey B — Orchestration value:
Vague request → Intake/Scoping Agent drafts clarification → clock tolls → requester responds → Custodian/Search Agent fans out to departments → one custodian goes silent → reminder → escalation.
Purpose: proves dynamic case management, tolling, fan-out, and exception handling.
Journey C — Governance/redaction:
Search returns exemption-heavy docs → Review & Redaction Agent proposes Exemption 5/6/7(C) redactions with source spans and foreseeable-harm rationales → officer accepts some and rejects one → agent revises → release-integrity guard blocks unapproved output → human approval → release succeeds.
Purpose: proves HITL, legal accountability, auditability, and safe release.



12. Repository (monorepo, Python)
foia-case-system/
├── README.md                  # problem, architecture, run instructions, demo link
├── docs/
│   ├── design-brief.md         # this document
│   └── coding-design.md        # the §8 invariants → feeds AGENTS.md
├── shared/
│   └── contracts/              # request/status/case + rule schemas (one source of truth)
├── agents/
│   ├── scoping-agent/          # own uipath.json, main.py, AGENTS.md — packages independently
│   ├── review-redaction-agent/
│   └── custodian-search-agent/
├── policy-packs/
│   └── federal-foia/           # versioned PolicyProvider pack (id + version + effective_date)
├── portal/ # requester-facing web client (calls UiPath APIs) 
├── maestro/ # exported Maestro case/process model + README (authored in Studio                                     Web, not Python) 
└── workflows/ # exported API Workflow definitions + README (authored in Studio Web, not Python)

Monorepo, but each coded agent is its own independently-packageable UiPath project.

13. Tech stack & platform mapping
Language: Python. Agents: three LangGraph coded agents via the UiPath Python SDK, packaged with the UiPath CLI and deployed to Orchestrator.
Orchestration: Maestro Case. HITL: Action Center. Mechanical steps: API Workflows. Intake extraction: Document Understanding if available, deterministic parser/seeded JSON fallback if not.
Retrieval: MVP append-only correction log; optional stretch via LlamaIndex for corrections + precedent.
LLMs: agents call Claude directly via the Anthropic API, with the API key stored as an Orchestrator secret asset and the model chosen per step via config (env var / provider config). Model IDs must not be hardcoded in business logic. Routing LLM calls through the UiPath LLM Gateway / AI Trust Layer is an OPTIONAL stretch for additional platform-usage credit — pursue it only after the three journeys run and only if the chosen model IDs are confirmed available through the gateway; otherwise keep the direct Anthropic path to avoid a model-availability stall.
Record store: local folders or Google Drive folders-per-department behind the RecordStore interface.
Build with Claude Code as the coding-agent bonus path; document coding-agent usage in README and demo.

14. Build order
Milestone 0 — platform capability check
Verify, in THIS tenant, in priority order, and record each as PASS/FAIL in docs/platform-check.md:
Maestro Case is available and you can create a case/agentic process (BLOCKING — this is the Track 1 premise).
The Service Task 'Start and wait for agent' can target a deployed coded agent (BLOCKING — this is how Maestro invokes your agents).
Coded-agent deploy round-trips (uipath auth/init/pack/publish) and the agent shows in Orchestrator and runs on a serverless robot.
Action Center tasks can be created and completed.
API Workflows are available (NON-blocking — see fallback in build-order item 6).
Document Understanding is available (NON-blocking — DU is optional; default to the seeded-JSON fallback). ○ Fallback if Maestro Case is NOT enabled: request access from the organizers/UiPath immediately. If it cannot be enabled in time, orchestrate the identical flow as a Maestro agentic (BPMN) process — still on Automation Cloud, still Maestro — and emphasize the dynamic, exception-driven path (gateways, event branches, the toll, the silent-custodian escalation) to preserve as much of the case-management story as possible. Document this decision in ASSUMPTIONS.md. Do not silently proceed as if Case is available.
Milestone 1 — deployable three-agent skeleton
Smoke-test the deploy pipeline FIRST. Scaffold one trivial hello-world LangGraph coded agent → uipath init/pack/publish → confirm it runs on Automation Cloud and shows in Orchestrator. Do not build real logic until this round-trips. On an unfamiliar platform with a two-week clock, the pipeline is the risk, not the logic.
Intake/Scoping Agent returns a structured ScopedRequest.
Custodian/Search Agent returns structured SearchTask[].
Review & Redaction Agent returns structured RedactionProposal[].
Do not build advanced reasoning until all three agents deploy and run.
Do not build advanced reasoning until all three agents deploy and run.
Milestone 2 — end-to-end case spine
Wire the agents through Maestro Case:
request intake → scoping → custodian tasking → record query → redaction proposal → Action Center approval → release check.


Milestone 3 — three journeys
Add Journey A, Journey B, and Journey C with seeded demo data.
Milestone 4 — safeguards
Add typed validation, idempotency keys, release-integrity guard, and audit timeline.
Milestone 5 — polish
Add corrections-memory retrieval, self-consistency sampling, portal polish, DU integration, and final video/deck assets.

15. Hackathon constraints
Track 1 (Maestro Case); must run on Automation Cloud and be shown running in the ≤5-minute demo video (not slides); use multiple platform components deliberately (Maestro + coded agents + Action Center + DU/API Workflows); coded agents built with Claude Code earn the bonus; public GitHub repo + README required; presentation deck required. Deadline: June 29.

