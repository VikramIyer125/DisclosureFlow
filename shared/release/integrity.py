"""Release-integrity guard — only approved bytes can ever leave (brief §8.4).

A standalone PURE FUNCTION, not a pydantic validator. Given a candidate
`ReleasePackage` and the `ApprovedRedaction`s it was assembled from, plus the
actual assembled bytes, it verifies:

  1. every applied redaction carries a non-empty `approval_token` (tied to a
     specific human approval);
  2. each applied redaction's `approved_content_hash` is a well-formed sha256
     and matches the per-record expectation the caller supplies;
  3. the recomputed sha256 of the assembled bytes equals `package.package_hash`.

On ANY failure it RETURNS a block result (it does not raise), so the caller can
route the case to a human (§8.2 dead-letter / close-out queue) with full context.
Returning a structured result rather than raising keeps the guard a pure
decision the case model can branch on.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from ..contracts import ApprovedRedaction, ReleasePackage

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ReleaseBlockReason = str


@dataclass(frozen=True)
class ReleaseGuardResult:
    """Outcome of the §8.4 guard. `allowed` False ⇒ block; reasons explain why."""

    allowed: bool
    reasons: list[ReleaseBlockReason] = field(default_factory=list)

    def __bool__(self) -> bool:  # convenience: `if guard_result:` ⇒ allowed
        return self.allowed


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.match(value))


def check_release_integrity(
    package: ReleasePackage,
    approved_redactions: list[ApprovedRedaction],
    assembled_bytes: bytes,
    expected_content_hashes: Optional[Mapping[str, str]] = None,
) -> ReleaseGuardResult:
    """Verify a release package before any bytes leave the system (§8.4).

    Args:
        package: The candidate `ReleasePackage` about to be released.
        approved_redactions: The approvals the package was assembled from. Each
            must be 'approved' or 'edited' (a 'rejected' redaction must not be
            baked in) and carry a valid token + approved_content_hash.
        assembled_bytes: The actual bytes that would be released; their sha256
            must equal `package.package_hash`.
        expected_content_hashes: Optional map record_ref -> expected
            approved_content_hash, letting the caller assert each approved
            redaction matches the record it was approved against. When omitted,
            only token presence and hash well-formedness are checked.

    Returns:
        `ReleaseGuardResult(allowed=True)` if every check passes, else
        `allowed=False` with one reason per failure. Never raises on a policy
        failure (only on nothing — programming errors still surface naturally).
    """
    reasons: list[str] = []

    # The package may only contain redactions that were actually approved/edited.
    applied = package.applied_redactions
    approved_by_key = {
        (a.record_ref, a.approval_token): a for a in approved_redactions
    }

    if not applied:
        # A package with no applied redactions is permitted (e.g. Journey A: no
        # exemptions) — that is not a failure. Hash check below still runs.
        pass

    for ar in applied:
        ctx = f"record={ar.record_ref!r}"

        if ar.decision == "rejected":
            reasons.append(f"{ctx}: a rejected redaction must not be baked into the release")
            continue

        if not ar.approval_token:
            reasons.append(f"{ctx}: missing approval_token (no human approval tied to this redaction)")
        elif (ar.record_ref, ar.approval_token) not in approved_by_key:
            reasons.append(
                f"{ctx}: approval_token does not match any supplied ApprovedRedaction for this record"
            )

        if not _is_sha256(ar.approved_content_hash):
            reasons.append(f"{ctx}: approved_content_hash is not a well-formed sha256")
        elif expected_content_hashes is not None:
            expected = expected_content_hashes.get(ar.record_ref)
            if expected is None:
                reasons.append(f"{ctx}: no expected content hash supplied for this record")
            elif expected != ar.approved_content_hash:
                reasons.append(
                    f"{ctx}: approved_content_hash {ar.approved_content_hash} != expected {expected}"
                )

    # Package-byte integrity: recomputed hash must equal the declared package_hash.
    if not _is_sha256(package.package_hash):
        reasons.append("package_hash is not a well-formed sha256")
    else:
        recomputed = _sha256_hex(assembled_bytes)
        if recomputed != package.package_hash:
            reasons.append(
                f"assembled-bytes hash {recomputed} != declared package_hash {package.package_hash}"
            )

    return ReleaseGuardResult(allowed=not reasons, reasons=reasons)


__all__ = ["check_release_integrity", "ReleaseGuardResult", "ReleaseBlockReason"]
