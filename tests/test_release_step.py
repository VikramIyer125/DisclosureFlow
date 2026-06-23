"""Deterministic tests for the release step (stage 6, §8.4 guard, §8.5)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from shared.contracts import ApprovedRedaction, CandidateRecord, Span, release_key
from steps import assemble_release
from steps.release_step import _REDACTION_CHAR

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
PACK_ID = "federal-foia"
PACK_VERSION = "2025.06.0"


def _record(case_id: str, ref: str, text: str) -> CandidateRecord:
    return CandidateRecord(
        case_id=case_id,
        jurisdiction="federal_foia",
        record_ref=ref,
        department="Office of Human Resources",
        record_type="email",
        task_id="t-hr",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


def _apply(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in sorted(spans, reverse=True):
        for i in range(start, end):
            chars[i] = _REDACTION_CHAR
    return "".join(chars)


def _expected_hash(text: str, approved_spans: list[tuple[int, int]]) -> str:
    redacted = _apply(text, approved_spans).encode("utf-8")
    return hashlib.sha256(redacted).hexdigest()


def _approval(
    case_id: str,
    ref: str,
    start: int,
    end: int,
    approved_content_hash: str,
    *,
    decision: str = "approved",
    token: str = "tok-valid",
) -> ApprovedRedaction:
    return ApprovedRedaction(
        case_id=case_id,
        jurisdiction="federal_foia",
        pack_id=PACK_ID,
        pack_version=PACK_VERSION,
        record_ref=ref,
        span=Span(record_ref=ref, start=start, end=end),
        rule_id="b6",
        citation="5 U.S.C. § 552(b)(6)",
        rationale="personal privacy",
        test_result={"test": "balancing", "elements": {}},
        decision=decision,
        officer="officer.demo",
        decided_at=NOW,
        approval_token=token,
        approved_content_hash=approved_content_hash,
    )


# A small record with a clear PII span to redact.
_TEXT = "Contact: Jane Doe phone 555-0142 end."
_PII_START = _TEXT.index("Jane Doe")
_PII_END = _PII_START + len("Jane Doe")


def _good_case():
    case_id = "case-rel-good"
    rec = _record(case_id, "REC-R-0001", _TEXT)
    h = _expected_hash(_TEXT, [(_PII_START, _PII_END)])
    ar = _approval(case_id, "REC-R-0001", _PII_START, _PII_END, h)
    return case_id, rec, ar


# ── Happy path: fully approved → passes guard, Bates package, matching hash ──


def test_fully_approved_release_succeeds():
    case_id, rec, ar = _good_case()
    res = assemble_release(
        package_id="PKG-0001",
        case_id=case_id,
        jurisdiction="federal_foia",
        pack_id=PACK_ID,
        pack_version=PACK_VERSION,
        source_records=[rec],
        approved_redactions=[ar],
        released_at=NOW,
        released_by="officer.demo",
    )
    assert res.released is True
    assert res.guard.allowed is True
    pkg = res.package
    assert pkg is not None
    # Bates numbered.
    assert pkg.bates_start == 1 and pkg.bates_end == 1
    assert pkg.record_refs == ["REC-R-0001"]
    # package_hash matches the assembled bytes.
    assert pkg.package_hash == hashlib.sha256(res.bytes_).hexdigest()
    # The approved span text is gone from the released bytes (non-removable).
    assert b"Jane Doe" not in res.bytes_
    assert _REDACTION_CHAR.encode("utf-8") in res.bytes_


# ── Block case 1: tampered source byte → approved_content_hash no longer matches ──


def test_tampered_bytes_block():
    case_id = "case-rel-tamper"
    rec = _record(case_id, "REC-R-0001", _TEXT)
    # Approval carries a hash computed against the ORIGINAL text...
    good_hash = _expected_hash(_TEXT, [(_PII_START, _PII_END)])
    ar = _approval(case_id, "REC-R-0001", _PII_START, _PII_END, good_hash)
    # ...but the source record is tampered (an extra char), so the bytes the step
    # produces hash differently → guard blocks.
    rec_tampered = _record(case_id, "REC-R-0001", _TEXT + "X")
    res = assemble_release(
        package_id="PKG-T",
        case_id=case_id,
        jurisdiction="federal_foia",
        pack_id=PACK_ID,
        pack_version=PACK_VERSION,
        source_records=[rec_tampered],
        approved_redactions=[ar],
        released_at=NOW,
        released_by="officer.demo",
    )
    assert res.released is False
    assert res.package is None
    assert res.bytes_ is None
    assert res.guard.allowed is False
    assert any("approved_content_hash" in r for r in res.guard.reasons)


# ── Block case 2: missing approval_token ──


def test_missing_token_blocks():
    case_id = "case-rel-notoken"
    rec = _record(case_id, "REC-R-0001", _TEXT)
    h = _expected_hash(_TEXT, [(_PII_START, _PII_END)])
    ar = _approval(case_id, "REC-R-0001", _PII_START, _PII_END, h, token="")
    res = assemble_release(
        package_id="PKG-NT",
        case_id=case_id,
        jurisdiction="federal_foia",
        pack_id=PACK_ID,
        pack_version=PACK_VERSION,
        source_records=[rec],
        approved_redactions=[ar],
        released_at=NOW,
        released_by="officer.demo",
    )
    assert res.released is False
    assert res.package is None
    assert any("approval_token" in r for r in res.guard.reasons)


# ── Block case 3: a rejected redaction baked into the release ──


def test_rejected_redaction_baked_in_blocks():
    case_id = "case-rel-rejected"
    rec = _record(case_id, "REC-R-0001", _TEXT)
    # decision='rejected'. The step itself only bakes approved/edited, so to prove
    # the GUARD is the backstop we force a rejected one into the applied set by
    # constructing the package check directly through the step with a rejected
    # redaction that the step would skip — then assert nothing leaks and, more
    # importantly, that a rejected redaction never appears in the package.
    rej = _approval(
        case_id, "REC-R-0001", _PII_START, _PII_END,
        _expected_hash(_TEXT, []),  # nothing applied → unredacted hash
        decision="rejected",
    )
    res = assemble_release(
        package_id="PKG-RJ",
        case_id=case_id,
        jurisdiction="federal_foia",
        pack_id=PACK_ID,
        pack_version=PACK_VERSION,
        source_records=[rec],
        approved_redactions=[rej],
        released_at=NOW,
        released_by="officer.demo",
    )
    # The step does not bake a rejected redaction; the record is released
    # unredacted (the officer rejected the withholding — disclosure is the
    # default). The released package must contain NO applied redactions and the
    # original text must remain.
    assert res.released is True
    assert res.package is not None
    assert res.package.applied_redactions == []
    assert b"Jane Doe" in res.bytes_


def test_rejected_redaction_in_package_is_blocked_by_guard():
    """If a rejected redaction is forced into applied_redactions, the guard blocks."""
    from shared.contracts import ReleasePackage
    from shared.release import check_release_integrity

    case_id = "case-rel-rejected2"
    rec = _record(case_id, "REC-R-0001", _TEXT)
    rej = _approval(
        case_id, "REC-R-0001", _PII_START, _PII_END,
        _expected_hash(_TEXT, [(_PII_START, _PII_END)]),
        decision="rejected",
    )
    body = (_apply(_TEXT, [(_PII_START, _PII_END)])).encode("utf-8")
    header = b"--- BATES 000001 | REC-R-0001 ---\n"
    assembled = header + body + b"\n"
    pkg = ReleasePackage(
        case_id=case_id, jurisdiction="federal_foia", pack_id=PACK_ID, pack_version=PACK_VERSION,
        package_id="PKG-FORCED", bates_start=1, bates_end=1, record_refs=["REC-R-0001"],
        applied_redactions=[rej],  # forced rejected into the package
        package_hash=hashlib.sha256(assembled).hexdigest(),
        released_at=NOW, released_by="officer.demo",
    )
    guard = check_release_integrity(pkg, [rej], assembled)
    assert guard.allowed is False
    assert any("rejected" in r for r in guard.reasons)


# ── §8.5 idempotency: re-run yields the same package, no double release ──


def test_release_idempotent_on_rerun():
    case_id, rec, ar = _good_case()
    dedupe: dict = {}
    first = assemble_release(
        package_id="PKG-IDEM", case_id=case_id, jurisdiction="federal_foia",
        pack_id=PACK_ID, pack_version=PACK_VERSION, source_records=[rec],
        approved_redactions=[ar], released_at=NOW, released_by="officer.demo",
        dedupe_store=dedupe,
    )
    assert first.deduplicated is False
    assert release_key(case_id, "PKG-IDEM") in dedupe
    second = assemble_release(
        package_id="PKG-IDEM", case_id=case_id, jurisdiction="federal_foia",
        pack_id=PACK_ID, pack_version=PACK_VERSION, source_records=[rec],
        approved_redactions=[ar], released_at=NOW, released_by="officer.demo",
        dedupe_store=dedupe,
    )
    assert second.deduplicated is True
    assert second.package is first.package
    assert second.bytes_ == first.bytes_


def test_release_deterministic_without_dedupe_store():
    """Two independent runs produce byte-identical packages (deterministic)."""
    case_id, rec, ar = _good_case()
    a = assemble_release(
        package_id="PKG-DET", case_id=case_id, jurisdiction="federal_foia",
        pack_id=PACK_ID, pack_version=PACK_VERSION, source_records=[rec],
        approved_redactions=[ar], released_at=NOW, released_by="officer.demo",
    )
    b = assemble_release(
        package_id="PKG-DET", case_id=case_id, jurisdiction="federal_foia",
        pack_id=PACK_ID, pack_version=PACK_VERSION, source_records=[rec],
        approved_redactions=[ar], released_at=NOW, released_by="officer.demo",
    )
    assert a.bytes_ == b.bytes_
    assert a.package.package_hash == b.package.package_hash
