"""Idempotency-key helper for side-effecting actions (brief §8.5, spec item 6).

Keys are COMPUTED AT THE BOUNDARY, not stored on the data contracts. The
contracts carry the deterministic inputs (`clarification_round`, `task_id`,
`package_id`); the side-effecting step assembles the key from them right before
acting, so a replay computes the identical key and dedupes.

Shape: ``"<case_id>:<action>:<discriminator>"`` — e.g.
``"case123:clarification:round1"``. Only external-side-effect steps (mail, file
write, portal submit, record-store query) need this; Maestro's durable
execution covers in-graph replay.
"""

from __future__ import annotations

# Separator between key segments. A constant so producers and dedupe consumers
# agree; the segment values themselves must not contain it.
_SEP = ":"


def idempotency_key(case_id: str, action: str, discriminator: str) -> str:
    """Build a deterministic idempotency key for a side effect (§8.5).

    Args:
        case_id: The case the side effect belongs to.
        action: The action class, e.g. 'clarification', 'query', 'release',
            'reminder', 'escalation'.
        discriminator: What makes this occurrence unique within the action,
            e.g. 'round1' (clarification_round), a task_id, or a package_id.

    Returns:
        ``"<case_id>:<action>:<discriminator>"``.

    Raises:
        ValueError: if any segment is empty or contains the ':' separator,
            which would make the key ambiguous and defeat dedupe.
    """
    for label, value in (("case_id", case_id), ("action", action), ("discriminator", discriminator)):
        if not value:
            raise ValueError(f"idempotency_key segment {label!r} must be non-empty")
        if _SEP in value:
            raise ValueError(f"idempotency_key segment {label!r}={value!r} must not contain {_SEP!r}")
    return _SEP.join((case_id, action, discriminator))


def clarification_key(case_id: str, clarification_round: int) -> str:
    """Key for sending a clarification message (§5, §8.5).

    Produces the canonical ``"<case_id>:clarification:round<N>"`` shape from the
    `ScopedRequest.clarification_round` input.
    """
    if clarification_round < 1:
        raise ValueError("clarification_round must be >= 1 to send a clarification")
    return idempotency_key(case_id, "clarification", f"round{clarification_round}")


def query_key(case_id: str, task_id: str) -> str:
    """Key for a department record-store query side effect (§3, §8.5)."""
    return idempotency_key(case_id, "query", task_id)


def release_key(case_id: str, package_id: str) -> str:
    """Key for assembling/emitting a release package (§6, §8.5)."""
    return idempotency_key(case_id, "release", package_id)
