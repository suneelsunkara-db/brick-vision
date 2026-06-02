"""Runtime package availability checks."""

from __future__ import annotations


def runtime_available() -> bool:
    """Whether the ``brickvision_runtime.capability_graph`` package is on path."""

    try:
        import brickvision_runtime.capability_graph  # type: ignore[import-not-found]  # noqa: F401, PLC0415
    except Exception:
        return False
    return True

