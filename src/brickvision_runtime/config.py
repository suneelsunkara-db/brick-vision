"""YAML-backed BrickVision runtime configuration."""

from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
from typing import Any

import yaml


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceProfile:
    """Target workspace profile loaded from ``config/workspaces/*.workspace.yaml``."""

    profile_id: str
    partner_id: str
    display_name: str
    host: str
    workspace_id: str | None
    cloud: str | None
    region: str | None
    auth_mode: str
    read_only: bool
    allowed_catalogs: tuple[str, ...]
    blocked_catalogs: tuple[str, ...]
    include_system: bool
    config_hash: str
    path: str


def load_active_workspace_profile(
    *,
    config_dir: str | os.PathLike[str] | None = None,
    profile_id: str | None = None,
) -> WorkspaceProfile | None:
    """Load the active workspace profile, returning ``None`` if absent.

    ``.env`` points at the profile via ``BV_CONFIG_DIR`` and
    ``BV_ACTIVE_WORKSPACE_PROFILE``. Missing profiles are tolerated so older
    local setups can continue to run while the config-bundle model rolls out.
    """

    cfg_dir = Path(config_dir or os.environ.get("BV_CONFIG_DIR", "config"))
    active = profile_id or os.environ.get("BV_ACTIVE_WORKSPACE_PROFILE", "").strip()
    if not active:
        return None

    target = cfg_dir / "workspaces" / f"{active}.workspace.yaml"
    if not target.exists():
        return None

    raw = target.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"workspace profile must be a mapping: {target}")
    if payload.get("kind") != "workspace_profile":
        raise ValueError(f"workspace profile kind must be workspace_profile: {target}")

    workspace = _mapping(payload.get("workspace"))
    auth = _mapping(payload.get("auth"))
    scope = _mapping(payload.get("scope"))
    kg = _mapping(payload.get("kg"))
    introspection = _mapping(kg.get("introspection"))

    return WorkspaceProfile(
        profile_id=str(payload.get("id") or active),
        partner_id=str(payload.get("partner_id") or ""),
        display_name=str(payload.get("display_name") or active),
        host=str(workspace.get("host") or ""),
        workspace_id=_optional_str(workspace.get("workspace_id")),
        cloud=_optional_str(workspace.get("cloud")),
        region=_optional_str(workspace.get("region")),
        auth_mode=str(auth.get("mode") or "existing_databricks_env"),
        read_only=bool(scope.get("read_only", True)),
        allowed_catalogs=tuple(str(v) for v in scope.get("allowed_catalogs", []) or []),
        blocked_catalogs=tuple(str(v) for v in scope.get("blocked_catalogs", []) or []),
        include_system=bool(introspection.get("include_system", False)),
        config_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        path=str(target),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["WorkspaceProfile", "load_active_workspace_profile"]
