"""Pytest configuration. Make src/ + tests/fixtures/ importable without packaging."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
# N189 / discipline rule 15: test-only helpers (in-process telemetry
# aggregator, canonical-goal fixtures, future relocations) live under
# ``tests/fixtures/`` and are imported by their subdirectory name as
# top-level packages. Adding the ``tests/fixtures/`` directory to
# ``sys.path`` here keeps the import surface flat
# (``from telemetry_central.in_process_aggregator import ...``) and
# avoids forcing ``__init__.py`` at the ``tests/`` root, which would
# conflict with ruff's ``INP001`` per-file ignore for tests.
sys.path.insert(0, str(_REPO_ROOT / "tests" / "fixtures"))
