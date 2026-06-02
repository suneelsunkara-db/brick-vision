"""Runtime settings for the Console API sidecar.

Kept tiny and explicit. We deliberately do **not** load secrets or
OBO tokens here — those flow through request headers and are bound
to the per-request user identity (see ``identity.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Process-level settings.

    All values come from environment variables so the sidecar can run
    identically inside a Databricks App, a local Uvicorn dev server,
    or a CI container.
    """

    bv_catalog: str
    """BrickVision UC catalog (the ``BV_CATALOG`` env var)."""

    audit_schema: str
    """Schema housing ``audit.events`` (defaults to ``audit``)."""

    builds_schema: str
    """Schema housing ``builds.runs`` (defaults to ``builds``)."""

    cors_allow_origins: tuple[str, ...]
    """Tightly-scoped allowlist; defaults to the Vite dev origin."""

    spa_dist_dir: str | None
    """When set, the sidecar serves the built SPA from this dir."""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bv_catalog=os.environ.get("BV_CATALOG", "brickvision"),
            audit_schema=os.environ.get("BV_AUDIT_SCHEMA", "audit"),
            builds_schema=os.environ.get("BV_BUILDS_SCHEMA", "builds"),
            cors_allow_origins=tuple(
                o.strip()
                for o in os.environ.get(
                    "CONSOLE_API_CORS_ALLOW_ORIGINS",
                    "http://127.0.0.1:5173,http://localhost:5173",
                ).split(",")
                if o.strip()
            ),
            spa_dist_dir=os.environ.get("CONSOLE_API_SPA_DIST_DIR") or None,
        )


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    """Lazy, process-cached settings accessor."""

    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings.from_env()
    return _SETTINGS
