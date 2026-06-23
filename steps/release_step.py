"""Release STEP (stage 6) — deterministic Python behind the §8.4 release guard.

Brief §2 stage 6, §4, §8.4, §8.5. Assembles a `ReleasePackage` from
`ApprovedRedaction[]` and the source `CandidateRecord[]`, applying the approved
redactions to the source bytes (non-removable for the demo: the approved span
text is replaced with a fixed redaction marker), Bates-numbering, assembling the
package, and computing `package_hash`.

The hard rule (§8.4): only approved bytes leave. BEFORE producing the package
this step runs the committed `check_release_integrity` guard. The guard verifies
(1) every applied redaction carries a valid `approval_token` matching a supplied
approval, (2) no 'rejected' redaction is baked in, (3) each redaction's
`approved_content_hash` matches the hash of the per-record bytes this step
actually produced, and (4) `package_hash == sha256(assembled_bytes)`. On ANY
failure the guard returns a block result and this step BLOCKS — it returns a
`ReleaseStepResult` with `package=None` and the block reasons (route-to-human,
§8.2), and never emits unapproved bytes. It does not raise on a policy failure.

§8.5 idempotency. Release is side-effecting, keyed with
`release_key(case_id, package_id)`. Re-running with the same approvals and the
same `package_id` returns the identical package (byte-for-byte: deterministic
Bates ordering + deterministic hashing) from the dedupe store — no double-release.

The `approved_content_hash` an officer's `ApprovedRedaction` carries is the hash
of the exact post-redaction bytes the officer approved. The integrity chain is
therefore: source bytes (`CandidateRecord.content_hash`) → apply approved span →
post-redaction bytes whose hash MUST equal `approved_content_hash` → these bytes
are concatenated into the package → `package_hash`. A tampered source byte, a
missing token, or a rejected redaction each breaks the chain and blocks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import MutableMapping, Optional

from shared.contracts import (
    ApprovedRedaction,
    CandidateRecord,
    ReleasePackage,
    release_key,
)
from shared.release import ReleaseGuardResult, check_release_integrity

# Demo redaction-application convention: the approved span's characters are
# replaced 1:1 with this marker character, so the redaction is non-removable
# (the original bytes are gone from the released artifact) and the byte length
# is preserved (offsets of later spans on the same record stay valid). Logged in
# ASSUMPTIONS.md.
_REDACTION_CHAR = "█"  # FULL BLOCK '█'


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ReleaseStepResult:
    """Outcome of the release step.

    On success: `package` is the assembled `ReleasePackage` and `guard.allowed`
    is True. On a §8.4 block: `package` is None, `guard.allowed` is False, and
    `guard.reasons` explain why (the case model routes to a human, §8.2). `bytes_`
    is the assembled artifact on success, None on block (no unapproved bytes leave).
    `deduplicated` is True when a prior identical release was returned (§8.5).
    """

    guard: ReleaseGuardResult
    package: Optional[ReleasePackage] = None
    bytes_: Optional[bytes] = None
    deduplicated: bool = False

    @property
    def released(self) -> bool:
        """True only when the guard allowed and a package was produced."""
        return self.package is not None and self.guard.allowed


def _effective_span(ar: ApprovedRedaction) -> tuple[int, int]:
    """The (start, end) actually applied: the officer's edited span if 'edited'."""
    span = ar.edited_span if (ar.decision == "edited" and ar.edited_span is not None) else ar.span
    return span.start, span.end


def _apply_redactions_to_record(
    source_text: str, redactions: list[ApprovedRedaction]
) -> str:
    """Replace each approved/edited span's chars with the marker, length-preserving.

    Rejected redactions are skipped (their bytes stay in the record). Spans are
    applied right-to-left so earlier offsets are not shifted (length is preserved
    anyway, but right-to-left is robust if that ever changes).
    """
    applied = [r for r in redactions if r.decision in ("approved", "edited")]
    chars = list(source_text)
    for ar in sorted(applied, key=lambda r: _effective_span(r)[0], reverse=True):
        start, end = _effective_span(ar)
        start = max(0, start)
        end = min(len(chars), end)
        for i in range(start, end):
            chars[i] = _REDACTION_CHAR
    return "".join(chars)


def assemble_release(
    package_id: str,
    case_id: str,
    jurisdiction: str,
    pack_id: str,
    pack_version: str,
    source_records: list[CandidateRecord],
    approved_redactions: list[ApprovedRedaction],
    released_at,
    released_by: str,
    *,
    bates_start: int = 1,
    dedupe_store: Optional[MutableMapping[str, "ReleaseStepResult"]] = None,
) -> ReleaseStepResult:
    """Assemble + guard + release a package (§6, §8.4, §8.5).

    Args:
        package_id: Deterministic package id (feeds the §8.5 release key).
        case_id, jurisdiction, pack_id, pack_version: Identity + pack stamp for
            the `ReleasePackage` (§10 PackStamp).
        source_records: The `CandidateRecord`s whose bytes are released. Their
            `text` is the source the approved redactions are applied to; their
            order defines Bates ordering (one Bates number per record here).
        approved_redactions: Officer decisions. 'rejected' ones are NOT baked in;
            the guard also rejects any that slip through.
        released_at: Release timestamp (from the Clock seam).
        released_by: Releasing identity.
        bates_start: First Bates number (default 1).
        dedupe_store: §8.5 check-then-act map keyed by the release key. A repeat
            call with the same key returns the prior result (no double-release).

    Returns:
        `ReleaseStepResult`. On a guard block, `package` is None and no bytes are
        produced — the step never emits unapproved bytes.
    """
    key = release_key(case_id, package_id)
    store_map: MutableMapping[str, ReleaseStepResult] = {} if dedupe_store is None else dedupe_store

    # §8.5 check-then-act: identical re-run returns the prior outcome.
    if key in store_map:
        prior = store_map[key]
        return ReleaseStepResult(
            guard=prior.guard,
            package=prior.package,
            bytes_=prior.bytes_,
            deduplicated=True,
        )

    # Group approvals by the record they target.
    by_record: dict[str, list[ApprovedRedaction]] = {}
    for ar in approved_redactions:
        by_record.setdefault(ar.record_ref, []).append(ar)

    # Apply redactions per record, building per-record redacted bytes and the
    # expected post-redaction hash the guard will check the approvals against.
    record_refs: list[str] = []
    per_record_bytes: list[bytes] = []
    expected_content_hashes: dict[str, str] = {}

    for rec in source_records:
        redactions = by_record.get(rec.record_ref, [])
        source_text = rec.text or ""
        redacted_text = _apply_redactions_to_record(source_text, redactions)
        redacted_bytes = redacted_text.encode("utf-8")
        record_refs.append(rec.record_ref)
        per_record_bytes.append(redacted_bytes)
        # The hash the officer's approval MUST match: the exact bytes this step
        # produced from applying the approved span(s) to this record.
        expected_content_hashes[rec.record_ref] = _sha256_hex(redacted_bytes)

    # Bates-number: one Bates page per record, in source order. Assemble the
    # package bytes deterministically with a stamped header per record.
    bates_end = bates_start + max(len(source_records) - 1, 0)
    assembled_parts: list[bytes] = []
    for i, (ref, body) in enumerate(zip(record_refs, per_record_bytes)):
        bates = bates_start + i
        header = f"--- BATES {bates:06d} | {ref} ---\n".encode("utf-8")
        assembled_parts.append(header + body + b"\n")
    assembled_bytes = b"".join(assembled_parts)
    package_hash = _sha256_hex(assembled_bytes)

    # The redactions baked into the package: only approved/edited ones.
    applied = [ar for ar in approved_redactions if ar.decision in ("approved", "edited")]

    candidate_package = ReleasePackage(
        case_id=case_id,
        jurisdiction=jurisdiction,
        pack_id=pack_id,
        pack_version=pack_version,
        package_id=package_id,
        bates_start=bates_start,
        bates_end=bates_end,
        record_refs=record_refs,
        applied_redactions=applied,
        package_hash=package_hash,
        released_at=released_at,
        released_by=released_by,
    )

    # §8.4 guard BEFORE any bytes leave. We pass ALL supplied approvals (incl.
    # rejected) so the guard can catch a rejected one if it were baked in, and
    # the expected per-record hashes so it verifies approved_content_hash.
    guard = check_release_integrity(
        package=candidate_package,
        approved_redactions=approved_redactions,
        assembled_bytes=assembled_bytes,
        expected_content_hashes=expected_content_hashes,
    )

    if not guard.allowed:
        # BLOCK: emit no package and no bytes. Route-to-human (§8.2). Cache the
        # block so a deterministic re-run reports the same outcome.
        blocked = ReleaseStepResult(guard=guard, package=None, bytes_=None)
        store_map[key] = blocked
        return blocked

    result = ReleaseStepResult(guard=guard, package=candidate_package, bytes_=assembled_bytes)
    store_map[key] = result
    return result


__all__ = ["assemble_release", "ReleaseStepResult"]
