"""DisclosureFlow data contracts — one source of truth for stage-to-stage schemas.

Brief §10 pipeline:
    Request → ScopedRequest → SearchTask[] → CandidateRecord[]
    → RedactionProposal[] → ApprovedRedaction[] → ReleasePackage

The three coded agents, the requester portal, and the Maestro case model all
import these. Also exported: the supporting value types, the DERIVED confidence
function (§8.1), the §8.3 boundary validator, and the §8.5 idempotency-key
helpers. All are Pydantic v2 models configured for clean JSON round-trip across
the Maestro Service Task boundary.

Public import surface:
    from shared.contracts import RedactionProposal, derive_confidence, validate_proposal, ...
"""

from __future__ import annotations

from .confidence import (
    SELF_CONSISTENCY_AGREEMENT_THRESHOLD,
    derive_confidence,
)
from .identity import (
    FEDERAL_FOIA,
    ContractModel,
    IdentityEnvelope,
    PackStamp,
)
from .idempotency import (
    clarification_key,
    idempotency_key,
    query_key,
    release_key,
)
from .pipeline import (
    ApprovedRedaction,
    CandidateRecord,
    QueryResult,
    RedactionProposal,
    ReleasePackage,
    Request,
    ScopedRequest,
    SearchTask,
)
from .supporting import (
    ClarificationDraft,
    ConfidenceSignal,
    Correction,
    ExemptionTestResult,
    PackMetadata,
    QueryStatus,
    RecordContext,
    Rule,
    SearchTerms,
    SelfConsistencySignal,
    Span,
    TestElement,
)
from .validation import (
    Violation,
    ViolationKind,
    validate_proposal,
    validate_proposals,
    validate_test_completeness,
)

__all__ = [
    # ── Pipeline contracts (§10) ──────────────────────────────────────────────
    "Request",
    "ScopedRequest",
    "SearchTask",
    "CandidateRecord",
    "QueryResult",
    "RedactionProposal",
    "ApprovedRedaction",
    "ReleasePackage",
    # ── Identity / config ─────────────────────────────────────────────────────
    "ContractModel",
    "IdentityEnvelope",
    "PackStamp",
    "FEDERAL_FOIA",
    # ── Supporting value types ────────────────────────────────────────────────
    "Span",
    "RecordContext",
    "Rule",
    "PackMetadata",
    "SearchTerms",
    "QueryStatus",
    "TestElement",
    "ExemptionTestResult",
    "ConfidenceSignal",
    "SelfConsistencySignal",
    "Correction",
    "ClarificationDraft",
    # ── Derived confidence (§8.1) ─────────────────────────────────────────────
    "derive_confidence",
    "SELF_CONSISTENCY_AGREEMENT_THRESHOLD",
    # ── Typed-output validation (§8.3) ────────────────────────────────────────
    "validate_proposal",
    "validate_proposals",
    "validate_test_completeness",
    "Violation",
    "ViolationKind",
    # ── Idempotency keys (§8.5) ───────────────────────────────────────────────
    "idempotency_key",
    "clarification_key",
    "query_key",
    "release_key",
]
