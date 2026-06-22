"""DisclosureFlow seams — four swappable interfaces with demo + prod backings.

Brief §7: each seam is an injected dependency behind a `Protocol`, with a demo
backing (runnable where trivial) and a production backing (named stub). Pass
`jurisdiction` as a real parameter on every seam from day one.

  PolicyProvider    deterministic FOIA rules (NOT a vector store)
  RecordStore       N department repositories, controllable per-department behavior
  Clock             injected deadline service; holds no tolling/grace state
  CorrectionsMemory advisory, append-only grounded officer corrections

Public import surface:
    from shared.seams import (
        PolicyProvider, FederalFoiaPackProvider, RegistryPolicyProvider,
        RecordStore, LocalFolderRecordStore, DriveRecordStore, ConnectorRecordStore,
        Clock, ManualClock, SystemClock,
        CorrectionsMemory, AppendOnlyCorrectionLog, LlamaIndexCorrectionsMemory,
    )
"""

from __future__ import annotations

from .clock import (
    DEFAULT_GRACE_WINDOW_WORKING_DAYS,
    FOIA_RESPONSE_WORKING_DAYS,
    Clock,
    DeadlineStatus,
    ManualClock,
    SystemClock,
)
from .corrections_memory import (
    AppendOnlyCorrectionLog,
    CorrectionsMemory,
    LlamaIndexCorrectionsMemory,
    correction_key,
)
from .policy_provider import (
    FederalFoiaPackProvider,
    PolicyProvider,
    RegistryPolicyProvider,
)
from .record_store import (
    ConnectorRecordStore,
    DriveRecordStore,
    LocalFolderRecordStore,
    RecordStore,
)

__all__ = [
    # ── PolicyProvider (§7) ───────────────────────────────────────────────────
    "PolicyProvider",
    "FederalFoiaPackProvider",
    "RegistryPolicyProvider",
    # ── RecordStore (§7) ──────────────────────────────────────────────────────
    "RecordStore",
    "LocalFolderRecordStore",
    "DriveRecordStore",
    "ConnectorRecordStore",
    # ── Clock (§7) ────────────────────────────────────────────────────────────
    "Clock",
    "ManualClock",
    "SystemClock",
    "DeadlineStatus",
    "DEFAULT_GRACE_WINDOW_WORKING_DAYS",
    "FOIA_RESPONSE_WORKING_DAYS",
    # ── CorrectionsMemory (§7) ────────────────────────────────────────────────
    "CorrectionsMemory",
    "AppendOnlyCorrectionLog",
    "LlamaIndexCorrectionsMemory",
    "correction_key",
]
