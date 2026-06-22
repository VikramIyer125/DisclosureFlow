"""Typed-output validation at the agent boundary (brief §8.3, spec item 4).

The instant the Review & Redaction agent returns, every `RedactionProposal`
must be validated:
  1. `rule_id` ∈ the set of rules the PolicyProvider returned for this case;
  2. the rule's `required_test_elements` are all populated (not blank, not
     hedged) — completeness comes from the PACK, not hardcoded per-exemption
     fields;
  3. `record_ref` is in scope (one of the candidate records).

These are provided as PURE functions returning a list of `Violation`s — NOT a
pydantic model validator — because §8.3 says a failure is control flow
(re-prompt with the specific violation OR route to human-flagged), which sits
above the type. The caller decides the routing; this module only reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .pipeline import RedactionProposal
from .supporting import ExemptionTestResult, Rule

ViolationKind = Literal[
    "rule_id_not_in_set",
    "record_ref_out_of_scope",
    "missing_test_element",
    "blank_test_element",
    "hedged_test_element",
    "test_family_mismatch",
]


@dataclass(frozen=True)
class Violation:
    """A single §8.3 validation failure with enough context to re-prompt."""

    kind: ViolationKind
    detail: str
    record_ref: str
    rule_id: str


def validate_test_completeness(proposal: RedactionProposal, rule: Rule) -> list[Violation]:
    """Check a proposal's test result against the rule's required elements (§8.3.2).

    Reads the required element NAMES from `rule.required_test_elements` (the pack
    declares completeness) — never from hardcoded per-exemption fields. Returns
    one `Violation` per problem; empty list ⇒ the test is complete.
    """
    violations: list[Violation] = []
    result: ExemptionTestResult = proposal.test_result

    if result.test != rule.test:
        violations.append(
            Violation(
                kind="test_family_mismatch",
                detail=f"test_result.test={result.test!r} but rule {rule.id} expects {rule.test!r}",
                record_ref=proposal.record_ref,
                rule_id=rule.id,
            )
        )

    for name in rule.required_test_elements:
        element = result.elements.get(name)
        if element is None:
            violations.append(
                Violation(
                    kind="missing_test_element",
                    detail=f"required element {name!r} is absent",
                    record_ref=proposal.record_ref,
                    rule_id=rule.id,
                )
            )
            continue
        if not element.value.strip() or not element.evidence.strip():
            violations.append(
                Violation(
                    kind="blank_test_element",
                    detail=f"element {name!r} has blank value or evidence",
                    record_ref=proposal.record_ref,
                    rule_id=rule.id,
                )
            )
        if element.hedged:
            violations.append(
                Violation(
                    kind="hedged_test_element",
                    detail=f"element {name!r} is hedged",
                    record_ref=proposal.record_ref,
                    rule_id=rule.id,
                )
            )
    return violations


def validate_proposal(
    proposal: RedactionProposal,
    allowed_rules: Iterable[Rule],
    in_scope_record_refs: Iterable[str],
) -> list[Violation]:
    """Full §8.3 boundary validation of one proposal.

    Args:
        proposal: The Review agent's proposed redaction.
        allowed_rules: The exact set the PolicyProvider returned for this case.
            `rule_id` must be one of these ids (the "no invented exemptions"
            invariant); the matched rule then drives completeness.
        in_scope_record_refs: Record refs the agent was given (candidate set).

    Returns:
        A list of `Violation`s; empty ⇒ the proposal passes. Caller routes:
        re-prompt with the violations, or send to human-flagged.
    """
    violations: list[Violation] = []
    rules_by_id = {r.id: r for r in allowed_rules}

    # 1. rule_id ∈ PolicyProvider's returned set.
    rule = rules_by_id.get(proposal.rule_id)
    if rule is None:
        violations.append(
            Violation(
                kind="rule_id_not_in_set",
                detail=f"rule_id {proposal.rule_id!r} not in allowed set {sorted(rules_by_id)}",
                record_ref=proposal.record_ref,
                rule_id=proposal.rule_id,
            )
        )

    # 3. record_ref in scope.
    if proposal.record_ref not in set(in_scope_record_refs):
        violations.append(
            Violation(
                kind="record_ref_out_of_scope",
                detail=f"record_ref {proposal.record_ref!r} is not in the candidate set",
                record_ref=proposal.record_ref,
                rule_id=proposal.rule_id,
            )
        )

    # 2. required test elements populated (only meaningful if we resolved a rule).
    if rule is not None:
        violations.extend(validate_test_completeness(proposal, rule))

    return violations


def validate_proposals(
    proposals: Iterable[RedactionProposal],
    allowed_rules: Iterable[Rule],
    in_scope_record_refs: Iterable[str],
) -> dict[int, list[Violation]]:
    """Validate a batch; returns {index: violations} for proposals that failed.

    Indices with an empty/absent entry passed. Materializes `allowed_rules`
    and `in_scope_record_refs` once so generators aren't exhausted across items.
    """
    rules = list(allowed_rules)
    refs = set(in_scope_record_refs)
    failures: dict[int, list[Violation]] = {}
    for i, proposal in enumerate(proposals):
        v = validate_proposal(proposal, rules, refs)
        if v:
            failures[i] = v
    return failures
