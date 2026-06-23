"""DisclosureFlow mechanical seam STEPS — deterministic Python behind the seams.

Brief §4 / build-order: the record-store query (stage 3) and release/production
(stage 6) are mechanical, system-to-system steps. They are built FIRST as
deterministic Python behind the `RecordStore` seam and the §8.4 release guard so
the three journeys run end-to-end; LATER (a separate, gated step) they are
re-implemented as real UiPath API Workflows and wired as Maestro Service Tasks.

This module is NOT an API Workflow and does not pretend to be one — it is the
runnable seam-backed implementation the journeys use today.

Placement: a top-level `steps/` package (outside `shared/`, so it is not vendored
into every agent; see ASSUMPTIONS.md). Both callables consume and return the
`shared.contracts` shapes and apply the §8.5 idempotency keys at the boundary.

Public surface:
    from steps import run_record_query, run_record_queries, QueryDispatchResult
    from steps import assemble_release, ReleaseStepResult, load_record_store_for_journey
"""

from __future__ import annotations

from .record_query_step import (
    QueryDispatchResult,
    load_record_store_for_journey,
    run_record_queries,
    run_record_query,
)
from .release_step import (
    ReleaseStepResult,
    assemble_release,
)

__all__ = [
    "run_record_query",
    "run_record_queries",
    "QueryDispatchResult",
    "load_record_store_for_journey",
    "assemble_release",
    "ReleaseStepResult",
]
