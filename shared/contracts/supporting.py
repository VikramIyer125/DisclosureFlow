"""Supporting value types shared across pipeline stages (brief §7, §8, §10).

These are the small, reusable shapes that the seven pipeline contracts compose
from: spans, record context, the PolicyProvider `Rule`/`PackMetadata`, the
RecordStore `SearchTerms`/`QueryResult`, the generic data-driven exemption test
(`TestElement` / `ExemptionTestResult`), the *derived* `ConfidenceSignal`, the
corrections-log `Correction`, and the clarification draft.

Design notes tied to the brief:
- `ExemptionTestResult` is GENERIC and DATA-DRIVEN (spec item 4): the required
  element names come from `Rule.required_test_elements`, never hardcoded per
  exemption. This is what makes adding a new exemption a pack edit, not a code
  change (§7 extensibility).
- `ConfidenceSignal` is DERIVED, never an LLM-filled field (spec item 5, §8.1).
  The Review agent must compute it via `derive_confidence` (see
  ``shared.contracts.confidence``); it is recorded here only as evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from .identity import ContractModel

# ─────────────────────────────────────────────────────────────────────────────
# Spans and record context
# ─────────────────────────────────────────────────────────────────────────────


class Span(ContractModel):
    """A located region inside a record (the unit a redaction or correction targets).

    `unit` defaults to ``"char"``; ``"token"`` and ``"page_bbox"`` are carried so a
    future document-understanding backing can address regions natively without a
    contract change.
    """

    record_ref: str = Field(description="Stable id of the record this span lives in.")
    start: int = Field(ge=0, description="Inclusive start offset in `unit`s.")
    end: int = Field(ge=0, description="Exclusive end offset in `unit`s.")
    unit: Literal["char", "token", "page_bbox"] = Field(
        default="char", description="Addressing unit for start/end."
    )
    quote: Optional[str] = Field(
        default=None, description="Optional verbatim text of the span, for human review / audit."
    )


class RecordContext(ContractModel):
    """Context used to retrieve advisory corrections (§7 CorrectionsMemory).

    Intentionally lightweight: `record_type` + a `snippet` are enough for the
    MVP exact-match/recency retrieval; `embedding` stays ``None`` until the
    LlamaIndex stretch backing fills it. Carrying it now means the retrieval
    seam has something to key on without a later contract change.
    """

    record_type: str = Field(description="Coarse record class, e.g. 'email', 'memo', 'spreadsheet'.")
    snippet: Optional[str] = Field(default=None, description="Short text excerpt giving the model/officer context.")
    embedding: Optional[list[float]] = Field(
        default=None, description="Stretch only: vector for LlamaIndex retrieval. None in MVP."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PolicyProvider types (§7)
# ─────────────────────────────────────────────────────────────────────────────


class Rule(ContractModel):
    """A deterministic FOIA exemption rule returned by the PolicyProvider (§7, §8.3).

    The PolicyProvider is NOT a vector store: it returns the closed set of rules
    an agent may ground a withholding in. `required_test_elements` is what the
    §8.3 completeness validator reads — the pack declares what a complete legal
    test looks like, so new exemptions ship as data.
    """

    id: str = Field(strict=True, description="Stable rule id, e.g. 'b6'. Security-critical: strict.")
    citation: str = Field(description="Statutory citation, e.g. '5 U.S.C. § 552(b)(6)'.")
    text: str = Field(description="Human-readable rule text shown in review.")
    test: Literal["categorical", "balancing", "foreseeable_harm"] = Field(
        description="Legal test family. 'balancing' (e.g. b6/b7c) always forces full human review (§8.1a)."
    )
    foreseeable_harm: bool = Field(
        description="Whether a foreseeable-harm rationale is required to withhold under this rule."
    )
    required_test_elements: list[str] = Field(
        description="Element names a complete ExemptionTestResult must populate (drives §8.3 validation)."
    )


class PackMetadata(ContractModel):
    """Versioning metadata for a PolicyProvider pack (§7)."""

    pack_id: str = Field(strict=True, description="Pack id, e.g. 'federal-foia'.")
    version: str = Field(strict=True, description="Semantic version of the pack.")
    effective_date: datetime = Field(description="When this pack version takes effect.")
    jurisdiction: str = Field(description="Jurisdiction this pack serves, e.g. 'federal_foia'.")


# ─────────────────────────────────────────────────────────────────────────────
# RecordStore types (§7)
# ─────────────────────────────────────────────────────────────────────────────


QueryStatus = Literal["responded", "slow", "silent", "wrong_docs"]
"""Per-department demo behavior of a RecordStore query (§7).

Shared between `QueryResult.status` and the RecordStore seam so the controllable
custodian behavior (respond / slow / silent / wrong_docs) is one named type.
'slow'/'silent'/'wrong_docs' are the §2 exception branches the case model routes
(reminder → escalation); 'responded' is the happy path.
"""


class SearchTerms(ContractModel):
    """Search terms the Custodian/Search agent hands to a department repository (§7)."""

    keywords: list[str] = Field(default_factory=list, description="Free-text keywords to match.")
    date_from: Optional[datetime] = Field(default=None, description="Optional inclusive lower date bound.")
    date_to: Optional[datetime] = Field(default=None, description="Optional inclusive upper date bound.")
    record_types: list[str] = Field(
        default_factory=list, description="Optional record-type filter, e.g. ['email','memo']."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exemption test (generic, data-driven) — §8.3
# ─────────────────────────────────────────────────────────────────────────────


class TestElement(ContractModel):
    """One filled element of a legal test (§8.1b, §8.3).

    `hedged=True` or an empty `value`/`evidence` marks the element as incomplete,
    which the confidence derivation reads as low confidence and the §8.3 validator
    reads as a completeness violation.
    """

    value: str = Field(description="The agent's finding for this element.")
    evidence: str = Field(description="Source-grounded support for the finding.")
    hedged: bool = Field(default=False, description="True if the model hedged/was uncertain on this element.")


class ExemptionTestResult(ContractModel):
    """Generic, data-driven result of applying a rule's legal test (spec item 4).

    `elements` is keyed by element name; the *required* names come from
    `Rule.required_test_elements`, never hardcoded here. The §8.3 completeness
    check lives in ``shared.contracts.validation`` as a pure function, not a
    model validator, because a failure is control flow (re-prompt OR route to
    human) that sits above the type.
    """

    test: Literal["categorical", "balancing", "foreseeable_harm"] = Field(
        description="Which legal-test family was applied (mirrors the Rule.test)."
    )
    elements: dict[str, TestElement] = Field(
        default_factory=dict, description="element_name -> filled TestElement."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Confidence signal (derived, never LLM-filled) — §8.1
# ─────────────────────────────────────────────────────────────────────────────


class SelfConsistencySignal(ContractModel):
    """Optional self-consistency sampling result (§8.1c, stretch)."""

    samples: int = Field(ge=1, description="Number of samples drawn for the exemption classification.")
    agreement: float = Field(ge=0.0, le=1.0, description="Fraction of samples agreeing on the classification.")


class ConfidenceSignal(ContractModel):
    """DERIVED routing signal — never a field the LLM fills (spec item 5, §8.1).

    Produced only by ``derive_confidence`` (``shared.contracts.confidence``),
    which encodes the §8.1 priority order. Recorded on the proposal as evidence
    of *why* the proposal routes the way it does.
    """

    level: Literal["high", "low"] = Field(description="Routing level: 'low' forces close scrutiny / full review.")
    derivation: Optional[
        Literal[
            "balancing_always_full_review",
            "incomplete_test_elements",
            "self_consistency_disagreement",
        ]
    ] = Field(
        default=None,
        description=(
            "Which §8.1 rule forced 'low' (priority order a→b→c). None when level=='high' "
            "(no low-gate fired): the three values are reasons-for-low, so a high result has "
            "no honest value among them. See ASSUMPTIONS.md."
        ),
    )
    self_consistency: Optional[SelfConsistencySignal] = Field(
        default=None, description="Present only when self-consistency sampling ran (stretch)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Corrections (advisory) — §7
# ─────────────────────────────────────────────────────────────────────────────


class Correction(ContractModel):
    """An officer correction stored with grounding in the corrections log (§7).

    Advisory only: a stored correction informs a future proposal but can never
    set `rule_id` or confidence or bypass the PolicyProvider / human gate. The
    grounding fields exist so a future retrieval backing has something to
    retrieve and surface into human review.
    """

    direction: Literal["over_redaction_rejected", "missed_redaction_added"] = Field(
        description="Whether the officer removed an over-redaction or added a missed one."
    )
    record_context: RecordContext = Field(description="Context for retrieval (type + snippet/embedding).")
    rule_id: str = Field(strict=True, description="Rule the correction concerned (security-critical: strict).")
    rationale: str = Field(description="Officer's reasoning for the correction.")
    span: Span = Field(description="Region the correction applied to.")
    jurisdiction: str = Field(description="Jurisdiction at correction time.")
    pack_version: str = Field(description="PolicyProvider pack version at correction time.")
    officer: str = Field(description="Identity of the officer who made the correction.")
    timestamp: datetime = Field(description="When the correction was recorded.")


# ─────────────────────────────────────────────────────────────────────────────
# Clarification (Intake/Scoping) — §5
# ─────────────────────────────────────────────────────────────────────────────


class ClarificationDraft(ContractModel):
    """Agent-drafted narrowing suggestion for a vague request (§3, §5).

    Sending tolls the clock, so dispatch is gated (one-click human send or
    autonomous-but-logged). `clarification_round` is a deterministic input to
    the idempotency key for the send action (§8.5) — the key is *computed* at
    the boundary, not stored on the contract.
    """

    clarification_round: int = Field(ge=1, description="1-based round; feeds the idempotency key, not stored as one.")
    message: str = Field(description="Requester-facing narrowing message (drafted on Haiku per §9).")
    suggested_narrowing: Optional[str] = Field(
        default=None, description="The narrower scope offered as optional ('faster, or keep original')."
    )
