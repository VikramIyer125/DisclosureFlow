"""The seven stage-to-stage pipeline contracts (brief §10).

    Request → ScopedRequest → SearchTask[] → CandidateRecord[]
    → RedactionProposal[] → ApprovedRedaction[] → ReleasePackage

One source of truth consumed by the three coded agents, the portal, and the
Maestro case model. `Request` is pre-identity (the requester has no case id yet);
every contract from `ScopedRequest` onward mixes in `IdentityEnvelope`, and every
contract from `RedactionProposal` onward also carries `PackStamp` (spec item 2).

Security-critical fields use Strict types (spec item 1): `rule_id`, all hashes
(`content_hash`, `approved_content_hash`, `package_hash`), `approval_token`,
`package_id`. These feed the §8.3 typed-output validator and the §8.4
release-integrity guard; a wrong-typed value must fail at the boundary, not coerce.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, StrictStr

from .identity import ContractModel, IdentityEnvelope, PackStamp
from .supporting import (
    ClarificationDraft,
    ConfidenceSignal,
    ExemptionTestResult,
    QueryStatus,
    SearchTerms,
    Span,
)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Intake & perfection
# ─────────────────────────────────────────────────────────────────────────────


class Request(ContractModel):
    """Raw inbound public-records request (pre-identity; §10, §2 stage 1).

    No `IdentityEnvelope`: a case id is assigned when the case is opened. The
    Intake/Scoping agent consumes this and emits a `ScopedRequest`.
    """

    request_id: str = Field(description="Portal-assigned id for the submission.")
    requester: str = Field(description="Requester identity (stubbed in MVP, no real auth).")
    text: str = Field(description="Free-text request as submitted.")
    submitted_at: datetime = Field(description="Submission timestamp (from the Clock seam, not wall-clock).")
    attachments: list[str] = Field(default_factory=list, description="Optional attachment refs.")


class ScopedRequest(IdentityEnvelope):
    """Interpreted request with track + extracted fields (§2 stage 2, §10).

    Emitted by the Intake/Scoping agent. `is_vague` drives the §5 clarification
    branch in the case model; `clarification_round` is the deterministic input
    that feeds the §8.5 idempotency key for the clarification send (not a key
    field itself — keys are computed at the boundary, spec item 6).

    `clarification` carries the agent's narrowing SUGGESTION and is present only
    when `is_vague` is True (None otherwise). This honors the §3 "never silently
    narrow" rule: the request's actual interpreted scope stays in `subject`,
    `record_types`, `extracted_fields`, etc.; the proposed narrower scope lives
    ONLY inside the draft, as a suggestion the requester can decline. The draft
    does not carry its own round — `clarification_round` above is the single
    canonical round (one source of truth for the §8.5 key).
    """

    request_id: str = Field(description="Originating Request.request_id.")
    track: Literal["fast_track", "standard", "complex"] = Field(
        description="Triage track classification (Haiku per §9)."
    )
    subject: str = Field(description="Normalized subject of the request.")
    extracted_fields: dict[str, str] = Field(
        default_factory=dict, description="Field extraction/normalization output (Haiku per §9)."
    )
    record_types: list[str] = Field(default_factory=list, description="Record types in scope, for the search stage.")
    departments_hint: list[str] = Field(
        default_factory=list, description="Optional departments the requester named; the search agent decides finally."
    )
    is_vague: bool = Field(default=False, description="True ⇒ case model routes to the §5 clarification loop.")
    clarification_round: int = Field(
        default=0, ge=0, description="0 if never clarified; ≥1 after each clarification. Feeds §8.5 keys."
    )
    clarification: Optional[ClarificationDraft] = Field(
        default=None,
        description=(
            "Agent's narrowing suggestion; present only when is_vague is True, None otherwise. "
            "Holds the proposed narrower scope so the original scope is never silently narrowed (§3)."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Search & custodian tasking
# ─────────────────────────────────────────────────────────────────────────────


class SearchTask(IdentityEnvelope):
    """One department search task in the custodian fan-out (§2 stage 3, §10).

    Emitted (as a list) by the Custodian/Search agent. `task_id` is the
    deterministic discriminator feeding the §8.5 idempotency key for the
    record-store query side effect.
    """

    task_id: str = Field(description="Deterministic id for this department task; feeds §8.5 query key.")
    department: str = Field(description="Target department repository.")
    terms: SearchTerms = Field(description="Search terms generated for this department.")


class CandidateRecord(IdentityEnvelope):
    """A record returned by a department repository (§2 stage 3, §10, §8.4).

    `content_hash` (sha256 of the source bytes) starts the release-integrity
    chain (§8.4) — every later hash is verified back toward it. `is_responsive`
    is ``None`` until the Review agent decides; ``True``/``False`` afterwards.
    """

    record_ref: str = Field(description="Stable id of the record within its repository.")
    department: str = Field(description="Department repository the record came from.")
    record_type: str = Field(description="Coarse record class, e.g. 'email'.")
    task_id: str = Field(description="SearchTask.task_id that surfaced this record.")
    content_hash: StrictStr = Field(
        description="sha256 of source bytes. Starts the §8.4 integrity chain. Strict: never coerce."
    )
    is_responsive: Optional[bool] = Field(
        default=None, description="None until Review decides responsiveness; then True/False."
    )
    text: Optional[str] = Field(default=None, description="Extracted text content, when available.")
    uri: Optional[str] = Field(default=None, description="Pointer to the source artifact.")


class QueryResult(IdentityEnvelope):
    """The outcome of one department record-store query (§7, §2 stage 3).

    Wraps the controllable demo behavior (`status`) with the records returned
    and the `task_id` for correlation. The case model reads `status` to drive the
    §2 exception branches: 'slow'/'silent' ⇒ reminder → escalation; 'wrong_docs'
    ⇒ the records are off-scope and Review will mark them non-responsive.
    """

    task_id: str = Field(description="SearchTask.task_id this result answers.")
    department: str = Field(description="Department repository queried.")
    status: QueryStatus = Field(description="Controllable demo behavior: responded|slow|silent|wrong_docs.")
    records: list[CandidateRecord] = Field(
        default_factory=list, description="Candidate records returned (empty for 'silent')."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 — Review & redaction proposal (the hero)
# ─────────────────────────────────────────────────────────────────────────────


class RedactionProposal(IdentityEnvelope, PackStamp):
    """A proposed withholding from the Review & Redaction agent (§3, §8.1, §8.3, §10).

    The hero contract. Every field that grounds a withholding is here: the
    `rule_id` (validated ∈ the PolicyProvider's returned set, §8.3), the
    `citation` and `rationale` (source-grounded, foreseeable-harm), the generic
    `test_result`, and the DERIVED `confidence`. `retrieved_corrections` are
    advisory only and surface into human review (§7); they never set rule_id or
    confidence.
    """

    record_ref: str = Field(description="Record this redaction applies to (validated in-scope, §8.3).")
    span: Span = Field(description="Region to redact.")
    rule_id: StrictStr = Field(
        description="PolicyProvider rule id grounding the withholding. Validated ∈ returned set (§8.3). Strict."
    )
    citation: str = Field(description="Statutory citation copied from the grounding Rule.")
    rationale: str = Field(description="Source-grounded, foreseeable-harm rationale for withholding.")
    test_result: ExemptionTestResult = Field(description="Generic, data-driven legal-test result (§8.3).")
    confidence: ConfidenceSignal = Field(
        description="DERIVED via derive_confidence (§8.1) — never set directly by the agent."
    )
    retrieved_corrections: list["Correction"] = Field(  # noqa: F821 - resolved at module end
        default_factory=list, description="Advisory corrections surfaced into review (§7). Never authoritative."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 5 — Human review gate
# ─────────────────────────────────────────────────────────────────────────────


class ApprovedRedaction(IdentityEnvelope, PackStamp):
    """An officer's decision on one proposed redaction (§5, §8.4, §10).

    Carries the proposal evidence plus the human decision. `approval_token` and
    `approved_content_hash` are the §8.4 inputs the release-integrity guard
    consumes — both Strict so a missing/garbled token cannot coerce into a
    truthy value. `edited_span` is set only when `decision == "edited"`.
    """

    record_ref: str = Field(description="Record the decision concerns.")
    span: Span = Field(description="Originally proposed span.")
    rule_id: StrictStr = Field(description="Grounding rule id from the proposal. Strict.")
    citation: str = Field(description="Statutory citation from the proposal.")
    rationale: str = Field(description="Rationale from the proposal (officer may have noted edits separately).")
    test_result: ExemptionTestResult = Field(description="Legal-test result from the proposal.")
    decision: Literal["approved", "rejected", "edited"] = Field(
        description="Officer decision. 'rejected'/'edited' feed the corrections log (§7) and revise loop (§2)."
    )
    officer: str = Field(description="Identity of the deciding officer.")
    decided_at: datetime = Field(description="When the decision was made (Clock seam).")
    officer_note: Optional[str] = Field(default=None, description="Optional officer note / correction rationale.")
    edited_span: Optional[Span] = Field(
        default=None, description="Set only when decision == 'edited': the officer-adjusted span."
    )
    approval_token: StrictStr = Field(
        description="Token tied to this specific human approval (§8.4). Strict: never coerce."
    )
    approved_content_hash: StrictStr = Field(
        description="sha256 of the exact approved post-redaction bytes (§8.4). Strict."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 6 — Release & production
# ─────────────────────────────────────────────────────────────────────────────


class ReleasePackage(IdentityEnvelope, PackStamp):
    """The assembled, Bates-numbered release artifact (§2 stage 6, §8.4, §10).

    Only ever produced from `ApprovedRedaction`s that pass the §8.4
    release-integrity guard. `package_id` and `package_hash` are Strict: the
    guard recomputes the assembled bytes' hash and compares against
    `package_hash`, and each applied redaction's `approved_content_hash` must
    verify. Any mismatch ⇒ the guard returns a block (it does not raise), and
    the case model routes to a human.
    """

    package_id: StrictStr = Field(description="Deterministic id of the release package. Strict; feeds §8.5 keys.")
    bates_start: int = Field(ge=0, description="First Bates number in the package.")
    bates_end: int = Field(ge=0, description="Last Bates number in the package.")
    record_refs: list[str] = Field(description="Records included in this release.")
    applied_redactions: list[ApprovedRedaction] = Field(
        description="The approved redactions baked into the package (each §8.4-verified)."
    )
    package_hash: StrictStr = Field(
        description="sha256 of the assembled package bytes (§8.4). Guard recomputes & compares. Strict."
    )
    released_at: datetime = Field(description="When the package was released (Clock seam).")
    released_by: str = Field(description="Identity that performed the release.")


# Resolve the forward reference to Correction without creating an import cycle at
# module top (Correction lives in supporting.py, which does not import pipeline).
from .supporting import Correction  # noqa: E402

RedactionProposal.model_rebuild()
