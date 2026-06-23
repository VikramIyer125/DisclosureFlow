"""Test bootstrap: put the repo root on sys.path so `shared` and `steps` import.

Mirrors the runtime convention (`from shared.contracts import ...`,
`from steps import ...`) without installing anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
