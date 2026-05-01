"""Pytest bootstrap: repo root must be on sys.path for `app` and `providers`."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_root_s = str(_ROOT)
if _root_s not in sys.path:
    sys.path.insert(0, _root_s)
