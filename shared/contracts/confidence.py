"""Deterministic confidence derivation (brief §8.1, spec item 5).

`ConfidenceSignal` is DERIVED, never a field the LLM fills. The Review &
Redaction agent must call `derive_confidence` and attach its result to each
`RedactionProposal`; it must never construct a `ConfidenceSignal` by hand. This
function encodes the §8.1 priority order so routing is reproducible and audit-able.

Priority order (first match wins):
  (a) balancing-test rule (e.g. b6 / b7c) ⇒ ALWAYS low / full human review,
      regardless of any other signal;
  (b) any required test element hedged or blank ⇒ low;
  (c) self-consistency disagreement (stretch, param) ⇒ low;
  otherwise ⇒ high.
"""

from __future__ import annotations

from typing import Optional

from .supporting import (
    ConfidenceSignal,
    ExemptionTestResult,
    Rule,
    SelfConsistencySignal,
)

# Default agreement threshold below which self-consistency sampling is treated
# as disagreement (§8.1c). Logged as a demo default in ASSUMPTIONS.md.
SELF_CONSISTENCY_AGREEMENT_THRESHOLD = 0.8


def _element_incomplete(result: ExemptionTestResult, required: list[str]) -> bool:
    """True if any required element is missing, blank, or hedged (§8.1b)."""
    for name in required:
        element = result.elements.get(name)
        if element is None:
            return True
        if element.hedged:
            return True
        if not element.value.strip() or not element.evidence.strip():
            return True
    return False


def derive_confidence(
    rule: Rule,
    test_result: ExemptionTestResult,
    self_consistency: Optional[SelfConsistencySignal] = None,
) -> ConfidenceSignal:
    """Derive the routing confidence for a proposal (§8.1).

    Args:
        rule: The grounding PolicyProvider rule (its `test` and
            `required_test_elements` drive (a) and (b)).
        test_result: The agent's filled, generic exemption test.
        self_consistency: Optional sampling result (stretch); when present and
            its agreement is below the threshold, triggers (c).

    Returns:
        A `ConfidenceSignal` whose `derivation` records which §8.1 rule fired.
    """
    # (a) Balancing-test exemptions always go to full human review.
    if rule.test == "balancing":
        return ConfidenceSignal(
            level="low",
            derivation="balancing_always_full_review",
            self_consistency=self_consistency,
        )

    # (b) Structured-test completeness: any hedged/blank required element ⇒ low.
    if _element_incomplete(test_result, rule.required_test_elements):
        return ConfidenceSignal(
            level="low",
            derivation="incomplete_test_elements",
            self_consistency=self_consistency,
        )

    # (c) Self-consistency disagreement (stretch).
    if (
        self_consistency is not None
        and self_consistency.agreement < SELF_CONSISTENCY_AGREEMENT_THRESHOLD
    ):
        return ConfidenceSignal(
            level="low",
            derivation="self_consistency_disagreement",
            self_consistency=self_consistency,
        )

    # Otherwise high confidence: no low-gate fired. `derivation` is None because
    # the three derivation values are all *reasons for low* — none honestly
    # labels a high result (see ASSUMPTIONS.md). Any self-consistency evidence
    # that ran and agreed is still attached for the audit trail.
    return ConfidenceSignal(
        level="high",
        derivation=None,
        self_consistency=self_consistency,
    )
