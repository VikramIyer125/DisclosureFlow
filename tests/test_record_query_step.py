"""Deterministic tests for the record-query step (stage 3, §7, §8.2, §8.5)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from shared.contracts import SearchTask, SearchTerms, query_key
from shared.seams import LocalFolderRecordStore
from steps import load_record_store_for_journey, run_record_queries, run_record_query

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEMO = _REPO_ROOT / "demo-data"


def _task(case_id: str, department: str, task_id: str, keywords: list[str]) -> SearchTask:
    return SearchTask(
        case_id=case_id,
        jurisdiction="federal_foia",
        task_id=task_id,
        department=department,
        terms=SearchTerms(keywords=keywords),
    )


# ── The four controllable behaviors produce the right status/classification ──


def test_responded_with_records():
    store = load_record_store_for_journey("A")
    t = _task("case-journey-A", "Office of Procurement", "t-proc", ["PR-2024-118"])
    out = run_record_query(t, store)
    assert out.result.status == "responded"
    assert out.classification == "records_returned"
    assert len(out.result.records) == 1


def test_responded_legitimate_negative_is_not_silent():
    """responded + 0 matches → legitimate_negative, distinct from silent (§8.2)."""
    store = load_record_store_for_journey("A")
    t = _task("case-journey-A", "Office of Procurement", "t-proc-miss", ["nonexistent-keyword-xyz"])
    out = run_record_query(t, store)
    assert out.result.status == "responded"
    assert out.classification == "legitimate_negative"
    assert out.is_legitimate_negative is True
    assert out.needs_escalation is False
    assert out.result.records == []


def test_silent_behavior():
    store = load_record_store_for_journey("B")
    t = _task("case-journey-B", "Office of Communications", "t-comms", ["IT", "modernization"])
    out = run_record_query(t, store)
    assert out.result.status == "silent"
    assert out.classification == "silent"
    assert out.needs_escalation is True
    assert out.result.records == []


def test_slow_behavior_still_returns_records():
    store = load_record_store_for_journey("B")
    t = _task("case-journey-B", "Office of the CIO", "t-cio", ["modernization"])
    out = run_record_query(t, store)
    assert out.result.status == "slow"
    assert out.classification == "records_returned"
    assert len(out.result.records) == 1


def test_wrong_docs_returns_offscope_records():
    store = LocalFolderRecordStore(
        root=_DEMO / "journey-B",
        behavior={"Office of Procurement": "wrong_docs"},
        case_id="case-journey-B",
    )
    # keyword that DOES match the file → wrong_docs returns the NON-matching set (empty here)
    t_match = _task("case-journey-B", "Office of Procurement", "t-wd-1", ["IT-MOD-2023-004"])
    out_match = run_record_query(t_match, store)
    assert out_match.result.status == "wrong_docs"
    # The one file matches, so the off-scope (non-matching) set is empty.
    assert out_match.result.records == []
    # keyword that does NOT match → wrong_docs returns the file as off-scope noise
    t_nomatch = _task("case-journey-B", "Office of Procurement", "t-wd-2", ["zzz-no-match"])
    out_nomatch = run_record_query(t_nomatch, store)
    assert out_nomatch.result.status == "wrong_docs"
    assert len(out_nomatch.result.records) == 1


# ── content_hash determinism + correctness (§8.4 chain root) ──


def test_content_hash_is_sha256_of_source_bytes():
    store = load_record_store_for_journey("A")
    t = _task("case-journey-A", "Office of Procurement", "t-proc", ["PR-2024-118"])
    rec = run_record_query(t, store).result.records[0]
    src = (_DEMO / "journey-A" / "Office of Procurement" / "REC-A-0001.txt").read_bytes()
    assert rec.content_hash == hashlib.sha256(src).hexdigest()
    # Provenance fields populated from the SearchTask.
    assert rec.task_id == "t-proc"
    assert rec.department == "Office of Procurement"
    assert rec.case_id == "case-journey-A"
    assert rec.jurisdiction == "federal_foia"


def test_content_hash_matches_review_fixture():
    """The seed yields the same content_hash the Review fixture already assumes."""
    store = load_record_store_for_journey("A")
    t = _task("case-journey-A", "Office of Procurement", "t-proc", ["PR-2024-118"])
    rec = run_record_query(t, store).result.records[0]
    assert rec.content_hash == "9d231fa8a14496931ed8a84dae253c7c2323d125af2710e32d57bb6393efedaa"


# ── §8.5 idempotency: query_key stable, re-dispatch dedupes ──


def test_query_key_stable_and_dedupes_on_redispatch():
    store = load_record_store_for_journey("A")
    t = _task("case-journey-A", "Office of Procurement", "t-proc", ["PR-2024-118"])
    dedupe: dict = {}
    first = run_record_query(t, store, dedupe_store=dedupe)
    assert first.deduplicated is False
    assert first.idempotency_key == query_key("case-journey-A", "t-proc")
    second = run_record_query(t, store, dedupe_store=dedupe)
    assert second.deduplicated is True
    assert second.idempotency_key == first.idempotency_key
    # Identical result object returned, not a fresh query.
    assert second.result is first.result


# ── Journey shapes ──


def test_journey_A_one_clean_record():
    store = load_record_store_for_journey("A")
    t = _task("case-journey-A", "Office of Procurement", "search-office-of-procurement", ["PR-2024-118"])
    out = run_record_query(t, store)
    assert out.classification == "records_returned"
    assert len(out.result.records) == 1
    # Clean: contains no exemption-trigger tokens used in Journey C.
    text = out.result.records[0].text
    for trigger in ("SSN", "PRE-DECISIONAL", "LAW ENFORCEMENT SENSITIVE"):
        assert trigger not in text


def test_journey_B_silent_and_slow_fire():
    store = load_record_store_for_journey("B")
    tasks = [
        _task("case-journey-B", "Office of Procurement", "search-office-of-procurement", ["modernization"]),
        _task("case-journey-B", "Office of the CIO", "search-office-of-the-cio", ["modernization"]),
        _task("case-journey-B", "Office of Communications", "search-office-of-communications", ["modernization"]),
    ]
    results = run_record_queries(tasks, store)
    by_dept = {r.result.department: r for r in results}
    assert by_dept["Office of the CIO"].result.status == "slow"
    assert by_dept["Office of the CIO"].classification == "records_returned"
    assert by_dept["Office of Communications"].result.status == "silent"
    assert by_dept["Office of Communications"].needs_escalation is True
    assert by_dept["Office of Procurement"].result.status == "responded"


def test_journey_C_records_contain_exemption_triggers():
    store = load_record_store_for_journey("C")
    tasks = [
        _task("case-journey-C", "Office of Human Resources", "search-office-of-human-resources", ["complaint", "Jane"]),
        _task("case-journey-C", "Office of the CIO", "search-office-of-the-cio", ["recommend", "modernization"]),
        _task("case-journey-C", "Office of the Inspector General", "search-office-of-the-inspector-general", ["investigation", "witness"]),
    ]
    results = run_record_queries(tasks, store)
    text_by_dept = {r.result.department: r.result.records[0].text for r in results if r.result.records}
    # b6 PII span present verbatim (Review locates spans by exact quote).
    assert "SSN 123-45-6789" in text_by_dept["Office of Human Resources"]
    # b5 deliberative / pre-decisional present.
    assert "PRE-DECISIONAL" in text_by_dept["Office of the CIO"]
    # b7c law-enforcement personal info present.
    assert "LAW ENFORCEMENT SENSITIVE" in text_by_dept["Office of the Inspector General"]
