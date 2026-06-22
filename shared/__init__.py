"""DisclosureFlow shared backbone (brief §7, §8, §10).

The single source of truth that every coded agent, the requester portal, and the
Maestro case model import:

  shared.contracts  — the seven stage-to-stage data contracts (§10) plus the
                      derived confidence fn (§8.1), the §8.3 boundary validator,
                      and the §8.5 idempotency-key helpers.
  shared.seams      — the four swappable seam Protocols (§7) with demo + prod
                      backings: PolicyProvider, RecordStore, Clock, CorrectionsMemory.
  shared.release    — the §8.4 release-integrity guard.

Import the submodules directly, e.g.::

    from shared.contracts import RedactionProposal, validate_proposal
    from shared.seams import FederalFoiaPackProvider, ManualClock
    from shared.release import check_release_integrity
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
