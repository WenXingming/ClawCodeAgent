"""pytest configuration: inject src/ into sys.path so test modules can import
from agent and core_contracts without explicit path manipulation.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    src_dir = str(Path(__file__).resolve().parent.parent.parent / 'src')
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


_ensure_src_on_path()
