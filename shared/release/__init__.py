"""Release-integrity guard (brief §8.4).

Public import surface:
    from shared.release import check_release_integrity, ReleaseGuardResult
"""

from __future__ import annotations

from .integrity import (
    ReleaseBlockReason,
    ReleaseGuardResult,
    check_release_integrity,
)

__all__ = ["check_release_integrity", "ReleaseGuardResult", "ReleaseBlockReason"]
