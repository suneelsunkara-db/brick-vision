"""Probe builders for the v0.7.7 Capability-Graph install pre-flights (N180).

Companion to ``capability_graph.py`` (pure check functions). The check
functions take typed ``*Spec`` + ``*Probe`` dataclasses and return
``list[PreFlightFailure]``; this module contains the **probe builders**
that collect observed workspace state via ``databricks.sdk`` and
populate those Probe shapes.

Why this is a separate module
=============================

Per the house style note in ``capability_graph.py``:

  > The probes are constructed by the install runner; this module
  > never imports ``databricks-sdk`` directly so unit tests can run
  > offline.

That keeps the pure check functions easy to unit-test exhaustively
(probe shapes are simple dicts/dataclasses), while the SDK-dependent
state collection is concentrated here behind a single
``BV_DRY_RUN=true`` env-gate. Discipline rule 15 compliant: no
Protocol seams, no mocks; ``databricks.sdk`` imports are lazy inside
function bodies; fixtures are real files on disk.

Dry-run fixtures
================

When ``BV_DRY_RUN=true`` each probe builder reads from a JSON fixture
under ``tests/fixtures/install_preflight/capability_graph/``:

- ``indexer_sp.json``       — SCIM ServicePrincipals query result
- ``budget_namespaces.json`` — ``<BV_CATALOG>.<BV_SCHEMA>.budget_namespaces`` rows
                              + per-SP env resolution
- ``uc_schema.json``        — UC schema ownership + grants
- ``vs_grants.json``        — VS endpoint + per-index grants

A probe builder returns ``None`` when the fixture is absent OR when
SDK calls fail in production — both equivalent to "observed state
unavailable", which the check functions treat as a failure
(``UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID`` etc.).

Wiring
======

The install CLI (``brickvision/cli/install.py``) wraps each probe
builder + check function pair into a ``PreFlight`` runner. The
runner is gated by ``BV_CAPABILITY_GRAPH_ENABLED`` (default
``true``); setting it to ``false`` skips all 4 capability-graph
gates (only useful for partners running pre-v0.7.7 installs).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .capability_graph import (
    BudgetNamespaceProbe,
    BudgetNamespaceSpec,
    IndexerSPProbe,
    IndexerSPSpec,
    UCSchemaProbe,
    UCSchemaSpec,
    VSGrantProbe,
    VSGrantSpec,
)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "install_preflight"
    / "capability_graph"
)


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "").lower() in ("1", "true", "yes")


def _resolve_fixture(name: str) -> Path:
    """Resolve the dry-run fixture path for a given probe.

    Override the per-probe path via ``BV_DRY_RUN_PREFLIGHT_<NAME>_PATH``;
    otherwise default to ``tests/fixtures/install_preflight/capability_graph/<name>.json``.
    """

    env_key = f"BV_DRY_RUN_PREFLIGHT_{name.upper()}_PATH"
    override = os.environ.get(env_key)
    if override:
        return Path(override)
    return _FIXTURE_ROOT / f"{name}.json"


def _load_fixture(name: str) -> Mapping[str, Any] | None:
    """Load a probe fixture; return ``None`` when missing or malformed."""

    target = _resolve_fixture(name)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _resolve_warehouse_id() -> str | None:
    return (
        os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
    )


def _resolve_catalog() -> str:
    return os.environ.get("BV_CATALOG", "brickvision")


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


# ---------------------------------------------------------------------------
# 1. Indexer SP probe
# ---------------------------------------------------------------------------


def build_indexer_sp_probe(*, spec: IndexerSPSpec) -> IndexerSPProbe | None:
    """Collect observed SCIM ServicePrincipals state.

    Production: queries
    ``GET /api/2.0/preview/scim/v2/ServicePrincipals``  filtered by
    display name. Dry-run: reads from ``indexer_sp.json``.

    Returns ``None`` on any SDK failure or missing dry-run fixture
    so the check function emits the SP-not-provisioned failure
    rather than crashing the install runner.
    """

    if _is_dry_run():
        payload = _load_fixture("indexer_sp")
        if payload is None:
            return None
        enabled = payload.get("enabled", {})
        if not isinstance(enabled, dict):
            enabled = {}
        return IndexerSPProbe(
            indexer_sp_application_id=payload.get("indexer_sp_application_id"),
            app_sp_application_id=payload.get("app_sp_application_id"),
            enabled={str(k): bool(v) for k, v in enabled.items()},
        )

    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415

        client = WorkspaceClient()
        # Lazy iteration over service principals; SDK paginates internally.
        indexer_app_id: str | None = None
        app_app_id: str | None = None
        enabled: dict[str, bool] = {}
        for sp in client.service_principals.list():
            display = getattr(sp, "display_name", None)
            app_id = getattr(sp, "application_id", None)
            active = bool(getattr(sp, "active", True))
            if display == spec.indexer_sp_display_name:
                indexer_app_id = app_id
                enabled[display] = active
            elif display == spec.app_sp_display_name:
                app_app_id = app_id
                enabled[display] = active
    except Exception:  # noqa: BLE001 — graceful degrade
        return None

    return IndexerSPProbe(
        indexer_sp_application_id=indexer_app_id,
        app_sp_application_id=app_app_id,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# 2. Budget namespace probe
# ---------------------------------------------------------------------------


def build_budget_namespace_probe(
    *, spec: BudgetNamespaceSpec
) -> BudgetNamespaceProbe | None:
    """Collect ``<BV_CATALOG>.<BV_SCHEMA>.budget_namespaces`` + per-SP env resolution.

    Production: Statement Execution. Dry-run:
    ``budget_namespaces.json`` shape:

    .. code-block:: json

        {
          "namespaces": {"app": "..._app_ledger", "indexer": "..._idx_ledger"},
          "env_resolution": {"bv_app_sp": "app", "bv_indexer_sp": "indexer"}
        }
    """

    if _is_dry_run():
        payload = _load_fixture("budget_namespaces")
        if payload is None:
            return None
        namespaces = payload.get("namespaces", {})
        env_resolution = payload.get("env_resolution", {})
        if not isinstance(namespaces, dict) or not isinstance(env_resolution, dict):
            return None
        return BudgetNamespaceProbe(
            namespaces={str(k): str(v) for k, v in namespaces.items()},
            env_resolution={str(k): str(v) for k, v in env_resolution.items()},
        )

    warehouse_id = _resolve_warehouse_id()
    if not warehouse_id:
        return None
    catalog = _resolve_catalog()
    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
        from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

        client = WorkspaceClient()
        response = client.statement_execution.execute_statement(
            statement=(
                f"SELECT namespace, ledger_table FROM {catalog}.{_resolve_schema()}.budget_namespaces"
            ),
            warehouse_id=warehouse_id,
            wait_timeout="50s",
        )
        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            return None
        rows = getattr(response.result, "data_array", None) or []
    except Exception:  # noqa: BLE001
        return None

    namespaces: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2 and row[0] is not None and row[1] is not None:
            namespaces[str(row[0])] = str(row[1])

    # env_resolution is best-effort — partner-specific install state
    # the install runner can't reliably introspect without exec'ing
    # the indexer Job's task code. We surface what's known and let
    # the check function flag any misresolutions; an empty map skips
    # the env-resolution branch entirely (treated as "we trust the
    # configured env vars").
    return BudgetNamespaceProbe(namespaces=namespaces, env_resolution={})


# ---------------------------------------------------------------------------
# 3. UC schema probe
# ---------------------------------------------------------------------------


def build_uc_schema_probe(*, spec: UCSchemaSpec) -> UCSchemaProbe | None:
    """Collect UC schema ownership + grants.

    Production: ``GET /api/2.1/unity-catalog/schemas/<schema>``  +
    ``GET /api/2.1/unity-catalog/permissions/schema/<schema>``. Dry-run:
    ``uc_schema.json`` shape:

    .. code-block:: json

        {
          "exists": true,
          "owner": "bv_indexer_sp",
          "grants": {"bv_app_sp": ["SELECT"], "bv_indexer_sp": ["ALL_PRIVILEGES"]}
        }
    """

    if _is_dry_run():
        payload = _load_fixture("uc_schema")
        if payload is None:
            return None
        grants_raw = payload.get("grants", {})
        if not isinstance(grants_raw, dict):
            return None
        grants = {
            str(p): tuple(str(x) for x in (privs or []))
            for p, privs in grants_raw.items()
        }
        return UCSchemaProbe(
            exists=bool(payload.get("exists", False)),
            owner=payload.get("owner") if isinstance(payload.get("owner"), str) else None,
            grants=grants,
        )

    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415

        client = WorkspaceClient()
        try:
            schema_info = client.schemas.get(full_name=spec.schema_full_name)
        except Exception:  # noqa: BLE001 — schema missing or no perms
            return UCSchemaProbe(exists=False, owner=None, grants={})

        owner = getattr(schema_info, "owner", None)
        # UC permissions API.
        perms_resp = client.grants.get(
            securable_type="schema",  # type: ignore[arg-type]
            full_name=spec.schema_full_name,
        )
        privilege_assignments = getattr(perms_resp, "privilege_assignments", None) or []
        grants: dict[str, tuple[str, ...]] = {}
        for assignment in privilege_assignments:
            principal = getattr(assignment, "principal", None)
            privileges = [
                str(getattr(p, "value", p))
                for p in (getattr(assignment, "privileges", None) or [])
            ]
            if principal:
                grants[str(principal)] = tuple(privileges)
    except Exception:  # noqa: BLE001
        return None

    return UCSchemaProbe(
        exists=True,
        owner=str(owner) if owner else None,
        grants=grants,
    )


# ---------------------------------------------------------------------------
# 4. VS grants probe
# ---------------------------------------------------------------------------


def build_vs_grants_probe(*, spec: VSGrantSpec) -> VSGrantProbe | None:
    """Collect VS endpoint + per-index grants.

    Production: ``databricks.vector_search.client.VectorSearchClient`` +
    ``GET /api/2.0/permissions/vector-search/index/...``. Dry-run:
    ``vs_grants.json`` shape:

    .. code-block:: json

        {
          "endpoint_exists": true,
          "index_grants": {
            "entity_index": {
              "bv_indexer_sp": ["WRITE"],
              "bv_app_sp": ["READ"]
            }
          }
        }
    """

    if _is_dry_run():
        payload = _load_fixture("vs_grants")
        if payload is None:
            return None
        index_grants_raw = payload.get("index_grants", {})
        if not isinstance(index_grants_raw, dict):
            return None
        index_grants: dict[str, dict[str, tuple[str, ...]]] = {}
        for index_name, per_principal in index_grants_raw.items():
            if not isinstance(per_principal, dict):
                continue
            normalized: dict[str, tuple[str, ...]] = {}
            for principal, privileges in per_principal.items():
                if not isinstance(privileges, (list, tuple)):
                    continue
                normalized[str(principal)] = tuple(str(p) for p in privileges)
            index_grants[str(index_name)] = normalized
        return VSGrantProbe(
            endpoint_exists=bool(payload.get("endpoint_exists", False)),
            index_grants=index_grants,
        )

    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415

        client = WorkspaceClient()
        # Endpoint existence check — list and match by name.
        endpoint_exists = False
        try:
            for ep in client.vector_search_endpoints.list_endpoints():
                if getattr(ep, "name", None) == spec.endpoint_name:
                    endpoint_exists = True
                    break
        except Exception:  # noqa: BLE001
            return None

        if not endpoint_exists:
            return VSGrantProbe(endpoint_exists=False, index_grants={})

        # Per-index grants. The Databricks SDK exposes index permissions
        # through the generic permissions API for vector-search securables.
        index_grants: dict[str, dict[str, tuple[str, ...]]] = {}
        for index_name in spec.capability_graph_indexes:
            full_name = f"{_resolve_catalog()}.{_resolve_schema()}.{index_name}"
            try:
                perms = client.grants.get(
                    securable_type="table",  # type: ignore[arg-type]
                    full_name=full_name,
                )
                privilege_assignments = (
                    getattr(perms, "privilege_assignments", None) or []
                )
                per_principal: dict[str, tuple[str, ...]] = {}
                for assignment in privilege_assignments:
                    principal = getattr(assignment, "principal", None)
                    privileges = [
                        str(getattr(p, "value", p))
                        for p in (getattr(assignment, "privileges", None) or [])
                    ]
                    if principal:
                        per_principal[str(principal)] = tuple(privileges)
                index_grants[index_name] = per_principal
            except Exception:  # noqa: BLE001
                index_grants[index_name] = {}
    except Exception:  # noqa: BLE001
        return None

    return VSGrantProbe(
        endpoint_exists=True,
        index_grants=index_grants,
    )


__all__ = [
    "build_budget_namespace_probe",
    "build_indexer_sp_probe",
    "build_uc_schema_probe",
    "build_vs_grants_probe",
]
