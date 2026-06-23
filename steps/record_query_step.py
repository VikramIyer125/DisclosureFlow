"""Record-query STEP (stage 3) — deterministic Python behind the RecordStore seam.

Brief §2 stage 3, §4, §7, §8.2, §8.5. Takes a `SearchTask` (or a list, the
custodian fan-out) and drives `RecordStore.query` over the demo backing, returning
`QueryResult`(s). The mechanical work the §4 API Workflow will LATER perform.

What this step guarantees on top of the raw seam call:

- §8.5 idempotency. The query is a side-effecting step, so it is keyed with
  `query_key(case_id, task_id)` and check-then-act'd against an injected
  dedupe store: a re-dispatched task with the same `task_id` returns the prior
  `QueryResult` instead of re-querying (no double dispatch). The default store is
  a process-local dict; a real deployment passes a durable map.

- §8.2 status semantics. TWO signals ride on the dispatch result and the case
  model reads BOTH:
    1. The raw `QueryResult.status` (preserved on `result.status`) is the full
       four-way behavior: 'responded' | 'slow' | 'silent' | 'wrong_docs'. The
       slow → reminder branch and the wrong_docs → Review-marks-non-responsive
       branch read THIS raw status, not the derived class below.
    2. The DERIVED `classification` is a narrower three-way escalation signal —
       `silent` | `legitimate_negative` | `records_returned` — that answers only
       "does this need escalation, was it a clean no-records negative, or did
       records come back?":
         * status=='silent'                  → 'silent'             → escalation
         * status in (responded|slow|wrong_docs) with 0 records
                                              → 'legitimate_negative' → NOT a failure (§8.2)
         * any status with ≥1 record         → 'records_returned'   → carry forward
  The key §8.2 guarantee: a 'silent' (no response) is NEVER collapsed into an
  empty 'responded' (legitimate-negative); they classify distinctly and drive
  different Maestro branches. For slow vs wrong_docs vs responded distinctions,
  branch on the raw `result.status`.

`content_hash`, `record_ref`, `department`, `record_type`, `task_id`, `text`, and
the `case_id`/`jurisdiction` identity are all populated by the seam backing
(`LocalFolderRecordStore`); this step does not recompute or mutate them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, MutableMapping, Optional

from shared.contracts import QueryResult, SearchTask, query_key
from shared.seams import LocalFolderRecordStore, RecordStore

# Where this file lives, so the demo-data root resolves regardless of cwd
# (agents reset cwd between calls — absolute resolution matters).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEMO_DATA = _REPO_ROOT / "demo-data"


# Derived, case-model-facing classification of a single query outcome.
DispatchClass = Literal[
    "records_returned",       # responded / slow / wrong_docs with ≥1 record
    "legitimate_negative",    # responded with 0 records — NOT a failure (§8.2)
    "silent",                 # no response from the custodian — escalate
]


@dataclass(frozen=True)
class QueryDispatchResult:
    """One task's query outcome plus the §8.2/§8.5 metadata the case model reads.

    `result` is the raw `QueryResult` from the seam. `classification` is the
    derived branch signal (see module docstring). `idempotency_key` is the §8.5
    key this dispatch was deduped on. `deduplicated` is True when the key was
    already seen and the prior result was returned instead of re-querying.
    """

    result: QueryResult
    classification: DispatchClass
    idempotency_key: str
    deduplicated: bool = False

    @property
    def needs_escalation(self) -> bool:
        """True when the custodian went silent (reminder → escalation branch)."""
        return self.classification == "silent"

    @property
    def is_legitimate_negative(self) -> bool:
        """True when the custodian responded with no matching records (§8.2)."""
        return self.classification == "legitimate_negative"


def _classify(result: QueryResult) -> DispatchClass:
    if result.status == "silent":
        return "silent"
    if not result.records:
        # responded / slow / wrong_docs but nothing came back. For 'responded'
        # this is the legitimate-negative case (§8.2). 'silent' is handled above.
        return "legitimate_negative"
    return "records_returned"


def run_record_query(
    task: SearchTask,
    store: RecordStore,
    *,
    dedupe_store: Optional[MutableMapping[str, QueryResult]] = None,
) -> QueryDispatchResult:
    """Dispatch one department search task through the RecordStore seam (§3, §8.5).

    Args:
        task: The `SearchTask` to run (carries case_id, jurisdiction, department,
            terms, and the deterministic `task_id`).
        store: The injected `RecordStore` backing (demo: `LocalFolderRecordStore`).
        dedupe_store: Optional check-then-act map keyed by the §8.5 query key.
            When the key is already present, the stored `QueryResult` is returned
            and no second query is issued (idempotent re-dispatch). Defaults to a
            fresh process-local dict (no cross-call dedupe) when None.

    Returns:
        A `QueryDispatchResult` wrapping the `QueryResult`, its derived
        classification, the idempotency key, and whether it was deduplicated.
    """
    key = query_key(task.case_id, task.task_id)
    store_map: MutableMapping[str, QueryResult] = {} if dedupe_store is None else dedupe_store

    # §8.5 check-then-act: re-dispatch with the same key returns the prior result.
    if key in store_map:
        prior = store_map[key]
        return QueryDispatchResult(
            result=prior,
            classification=_classify(prior),
            idempotency_key=key,
            deduplicated=True,
        )

    result = store.query(
        jurisdiction=task.jurisdiction,
        department=task.department,
        terms=task.terms,
        task_id=task.task_id,
    )
    store_map[key] = result
    return QueryDispatchResult(
        result=result,
        classification=_classify(result),
        idempotency_key=key,
        deduplicated=False,
    )


def run_record_queries(
    tasks: Iterable[SearchTask],
    store: RecordStore,
    *,
    dedupe_store: Optional[MutableMapping[str, QueryResult]] = None,
) -> list[QueryDispatchResult]:
    """Run the custodian fan-out: one query per task, sharing a dedupe store (§3).

    The shared `dedupe_store` is what makes re-dispatch of the whole fan-out
    idempotent — already-seen task_ids return their prior result.
    """
    shared_map: MutableMapping[str, QueryResult] = {} if dedupe_store is None else dedupe_store
    return [run_record_query(t, store, dedupe_store=shared_map) for t in tasks]


# ─────────────────────────────────────────────────────────────────────────────
# Demo wiring — build the seam backing for a named journey from demo-data/.
# ─────────────────────────────────────────────────────────────────────────────


def load_record_store_for_journey(
    journey: str,
    *,
    demo_data_root: Path | str | None = None,
    manifest: Path | str | None = None,
) -> LocalFolderRecordStore:
    """Build a `LocalFolderRecordStore` wired for a demo journey (§7, §11).

    Reads `demo-data/journeys.json` for the journey's folder root, case_id, and
    per-department behavior config, then constructs the seam backing pointed at
    `demo-data/<root>/`. This is demo wiring only — production passes a real
    `RecordStore` and never touches the seed corpus.

    Args:
        journey: Journey key, 'A' | 'B' | 'C'.
        demo_data_root: Override for the demo-data directory (default: repo demo-data/).
        manifest: Override for the journeys manifest (default: <demo_data_root>/journeys.json).

    Returns:
        A ready `LocalFolderRecordStore` with the journey's behavior dict and case_id.
    """
    root_dir = Path(demo_data_root) if demo_data_root is not None else _DEMO_DATA
    manifest_path = Path(manifest) if manifest is not None else root_dir / "journeys.json"
    spec = json.loads(manifest_path.read_text(encoding="utf-8"))
    journeys = spec["journeys"]
    if journey not in journeys:
        raise KeyError(f"unknown journey {journey!r}; known: {sorted(journeys)}")
    jspec = journeys[journey]
    return LocalFolderRecordStore(
        root=root_dir / jspec["root"],
        behavior=jspec["behavior"],
        case_id=jspec["case_id"],
    )


__all__ = [
    "run_record_query",
    "run_record_queries",
    "QueryDispatchResult",
    "DispatchClass",
    "load_record_store_for_journey",
]
