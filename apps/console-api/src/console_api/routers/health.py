"""Liveness + version router."""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..runtime_status import runtime_available

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict[str, object]:
    """Liveness probe.

    Reports the sidecar version and whether the
    ``brickvision_runtime`` package is wired up; both fields are
    surfaced in the SPA's "About" command-palette entry.
    """

    return {
        "ok": True,
        "version": __version__,
        "runtime_available": runtime_available(),
    }
