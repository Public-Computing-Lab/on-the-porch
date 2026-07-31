"""Shared import paths matching the repository's production bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT,
    ROOT / "api",
    ROOT / "on_the_porch",
    ROOT / "on_the_porch" / "rag stuff",
    ROOT / "scripts",
):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
