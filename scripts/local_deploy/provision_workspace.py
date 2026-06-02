"""Auto-provision the Databricks workspace prerequisites for v0.7.7
BrickVision capability indexing.

Topology this script wires up::

    +---------------------------+        +-------------------------------+
    | local SPA + FastAPI       |  HTTPS | Databricks workspace          |
    | (apps/console + sidecar)  | <----> |                               |
    +---------------------------+        |  bv_app_sp  bv_indexer_sp     |
                                         |     |             |            |
                                         |     v             v            |
                                         | <BV_CATALOG>.<BV_SCHEMA>.*     |
                                         |   (single flat schema; all    |
                                         |    Delta tables + the indexer-|
                                         |    state Volume live here —   |
                                         |    v0.7.7 consolidation)       |
                                         | bv_vs_endpoint                 |
                                         |   └─ entity_index              |
                                         |                                |
                                         | bv_capability_indexer (Job)    |
                                         +-------------------------------+

Phase contract (each phase is **idempotent** — re-runs are safe):

    1. ``ensure_service_principals``    (SCIM)
    2. ``ensure_warehouse``              (small serverless SQL warehouse)
    3. ``ensure_uc_catalog_schema``      (catalog + 1 schema + indexer-state Volume)
    4. ``ensure_capability_graph_ddl``   (13 Delta tables; renders ALL_DDL)
    4b. ``ensure_workspace_kg_ddl``      (Workspace KG Delta tables)
    5. ``ensure_budget_namespaces``      (config table + app/indexer rows)
    6. ``ensure_grants``                 (SP grants on schema + index)
    7. ``ensure_vector_search``          (endpoint + direct index; ~10 min cold)

Discipline rule 15 compliant: no Protocol seams, no mock classes; every
external call is a real ``databricks.sdk`` request lazy-imported inside
function bodies. ``BV_DRY_RUN=true`` is an inspection mode: doctor still
performs read-only checks, while provision exits before any workspace
write. Set ``BV_DRY_RUN=false`` for the real end-to-end deploy.

Invoke directly::

    python3 scripts/local_deploy/provision_workspace.py
    python3 scripts/local_deploy/provision_workspace.py --doctor   # diagnose only
    python3 scripts/local_deploy/provision_workspace.py --skip vs  # opt-out per phase
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


from scripts.local_deploy._lib import (  # noqa: E402
    LocalDeployConfig,
    assert_succeeded,
    configure_log_file,
    env_bool,
    execute_statement,
    load_dotenv,
    log,
    poll_until,
    workspace_client,
)


# ---------------------------------------------------------------------------
# Phase 1 — Service principals
# ---------------------------------------------------------------------------


def ensure_service_principals(
    *, client: Any, cfg: LocalDeployConfig
) -> dict[str, str]:
    """SCIM-create the indexer + app service principals if missing.

    Returns ``{display_name: application_id}``. Honours
    ``cfg.auto_provision_sps=False`` by skipping creation but still
    fetching application_ids (so downstream phases can still grant
    permissions on the existing SPs).
    """

    log("step", "Phase 1 — service principals", phase="sp")

    existing: dict[str, str] = {}
    for sp in client.service_principals.list():
        display = getattr(sp, "display_name", None)
        app_id = getattr(sp, "application_id", None)
        if display in (cfg.indexer_sp_name, cfg.app_sp_name) and app_id:
            existing[display] = app_id
            log("ok", f"found {display!r} (application_id={app_id})", phase="sp")

    needed = {cfg.indexer_sp_name, cfg.app_sp_name} - existing.keys()
    if not needed:
        return existing

    if not cfg.auto_provision_sps:
        raise SystemExit(
            f"✗ SPs missing: {sorted(needed)} but BV_LOCAL_DEPLOY_AUTO_PROVISION_SPS=false. "
            f"Either flip the flag or create them out-of-band."
        )

    for display in sorted(needed):
        log("info", f"creating SP {display!r}", phase="sp")
        sp = client.service_principals.create(display_name=display, active=True)
        existing[display] = getattr(sp, "application_id", "")
        log(
            "ok",
            f"created {display!r} (application_id={existing[display]})",
            phase="sp",
        )

    return existing


# ---------------------------------------------------------------------------
# Phase 2 — SQL warehouse
# ---------------------------------------------------------------------------


_WAREHOUSE_NAME = "bv-warehouse"


def ensure_warehouse(*, client: Any, cfg: LocalDeployConfig) -> str:
    """Resolve / create a SQL warehouse for Statement Execution.

    Order of resolution (first hit wins):
      1. ``DATABRICKS_WAREHOUSE_ID`` — canonical, declared in
         ``.env.example`` and read by the runtime + sidecar.
      2. ``BV_INDEXER_WAREHOUSE_ID`` — indexer-Job override (lets
         partners isolate the indexer to a dedicated warehouse for
         workload-management reasons).
      3. ``BV_WAREHOUSE_ID`` — legacy alias kept for back-compat.
      4. First existing warehouse named ``bv-warehouse``.
      5. If ``cfg.auto_provision_warehouse=True``: create one.

    The resolved id is written back to **all three** env vars so
    every downstream consumer (this script, ``brickvision install``,
    the FastAPI sidecar) finds it.
    """

    log("step", "Phase 2 — SQL warehouse", phase="whse")

    explicit = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
    )
    if explicit:
        log("ok", f"using explicit warehouse id={explicit}", phase="whse")
        os.environ["DATABRICKS_WAREHOUSE_ID"] = explicit
        os.environ["BV_INDEXER_WAREHOUSE_ID"] = explicit
        return explicit

    for wh in client.warehouses.list():
        if getattr(wh, "name", None) == _WAREHOUSE_NAME:
            wh_id = getattr(wh, "id", "")
            log("ok", f"found existing warehouse {_WAREHOUSE_NAME!r} (id={wh_id})", phase="whse")
            os.environ["DATABRICKS_WAREHOUSE_ID"] = wh_id
            os.environ["BV_INDEXER_WAREHOUSE_ID"] = wh_id
            return wh_id

    if not cfg.auto_provision_warehouse:
        raise SystemExit(
            "✗ No SQL warehouse resolved and BV_LOCAL_DEPLOY_AUTO_PROVISION_WAREHOUSE=false."
            " Either set BV_INDEXER_WAREHOUSE_ID in .env or flip the auto-provision flag."
        )

    log("info", f"creating serverless SQL warehouse {_WAREHOUSE_NAME!r}", phase="whse")
    from databricks.sdk.service.sql import (  # noqa: PLC0415
        CreateWarehouseRequestWarehouseType,
        EndpointInfoWarehouseType,
    )

    created = client.warehouses.create(
        name=_WAREHOUSE_NAME,
        cluster_size="2X-Small",
        min_num_clusters=1,
        max_num_clusters=1,
        auto_stop_mins=10,
        enable_serverless_compute=True,
        warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
    ).result()
    wh_id = getattr(created, "id", "")
    log("ok", f"warehouse {_WAREHOUSE_NAME!r} created (id={wh_id})", phase="whse")
    os.environ["DATABRICKS_WAREHOUSE_ID"] = wh_id
    os.environ["BV_INDEXER_WAREHOUSE_ID"] = wh_id
    _ = EndpointInfoWarehouseType  # silence unused-import lint
    return wh_id


# ---------------------------------------------------------------------------
# Phase 3 — UC catalog + single schema + Volume
# ---------------------------------------------------------------------------


def ensure_uc_catalog_schema(
    *, client: Any, cfg: LocalDeployConfig, warehouse_id: str, indexer_principal: str
) -> None:
    """Create ``<BV_CATALOG>``, the single ``<BV_SCHEMA>`` schema, and
    the indexer-state UC Volume (name from ``BV_INDEXER_STATE_VOLUME``,
    default ``indexer-state``) that the capability indexer stages
    inter-task JSON in
    (``/Volumes/<catalog>/<schema>/<state_volume>/runs/<run_id>/``).

    The Volume is **not** a state store — every typed capability-graph
    row lives in Delta tables; the Volume holds only short-lived
    per-run JSON hand-offs that the retention task GCs.

    The schema OWNER is set to the indexer service principal's
    application ID so the indexer SP can CREATE / DROP TABLE on its own state.
    The app SP gets SELECT on
    everything via Phase 6 (``ensure_grants``).
    """

    log("step", "Phase 3 — UC catalog + schema + indexer-state Volume", phase="uc")

    if not cfg.auto_provision_catalog:
        log("info", "skipping (BV_LOCAL_DEPLOY_AUTO_PROVISION_CATALOG=false)", phase="uc")
        return

    statements = []
    try:
        client.catalogs.get(cfg.catalog)
        log("ok", f"found existing catalog {cfg.catalog!r}; skipping CREATE CATALOG", phase="uc")
    except Exception:
        statements.append(
            f"CREATE CATALOG IF NOT EXISTS {cfg.catalog} COMMENT 'BrickVision install catalog (v0.7.7)'"
        )

    statements.extend([
        f"USE CATALOG {cfg.catalog}",
        (
            f"CREATE SCHEMA IF NOT EXISTS {cfg.catalog}.{cfg.schema}"
            f" COMMENT 'BrickVision substrate (single flat schema — v0.7.7"
            f" consolidation; every UC object lives here, table-name-prefixed"
            f" by domain)'"
        ),
        (
            f"CREATE VOLUME IF NOT EXISTS {cfg.catalog}.{cfg.schema}.{cfg.state_volume_name}"
            f" COMMENT 'Capability indexer per-run inter-task JSON hand-off"
            f" at /Volumes/{cfg.catalog}/{cfg.schema}/{cfg.state_volume_name}/runs/<run_id>/"
            f" (NOT a state store; auto-pruned by retention task)'"
        ),
    ])

    for stmt in statements:
        log("info", stmt, phase="uc")
        response = execute_statement(
            client, statement=stmt, warehouse_id=warehouse_id
        )
        assert_succeeded(response, statement_excerpt=stmt)

    owner_stmt = (
        f"ALTER SCHEMA {cfg.catalog}.{cfg.schema} OWNER TO `{indexer_principal}`"
    )
    log("info", owner_stmt, phase="uc")
    response = execute_statement(client, statement=owner_stmt, warehouse_id=warehouse_id)
    assert_succeeded(response, statement_excerpt=owner_stmt)
    log("ok", f"{cfg.catalog}.{cfg.schema} owned by {indexer_principal}", phase="uc")


# ---------------------------------------------------------------------------
# Phase 4 — Capability-graph DDL (13 Delta tables)
# ---------------------------------------------------------------------------


def ensure_capability_graph_ddl(
    *, client: Any, cfg: LocalDeployConfig, warehouse_id: str
) -> None:
    log("step", "Phase 4 — capability-graph DDL", phase="ddl")

    from brickvision_runtime.capability_graph.schemas import (  # noqa: PLC0415
        ALL_DDL,
        render,
    )

    for name, raw_ddl in ALL_DDL.items():
        statement = render(raw_ddl, cfg.catalog, cfg.schema)
        log("info", f"applying {cfg.catalog}.{cfg.schema}.{name}", phase="ddl")
        response = execute_statement(
            client, statement=statement, warehouse_id=warehouse_id
        )
        assert_succeeded(response, statement_excerpt=statement)

    log("ok", f"applied {len(ALL_DDL)} DDLs", phase="ddl")


def ensure_workspace_kg_ddl(
    *, client: Any, cfg: LocalDeployConfig, warehouse_id: str
) -> None:
    log("step", "Phase 4b — workspace-KG DDL", phase="kgddl")

    from brickvision_runtime.kg.schemas import ALL_DDL, render  # noqa: PLC0415

    for name, raw_ddl in ALL_DDL.items():
        statement = render(raw_ddl, cfg.catalog, cfg.schema)
        log("info", f"applying {cfg.catalog}.{cfg.schema}.{name}", phase="kgddl")
        response = execute_statement(
            client, statement=statement, warehouse_id=warehouse_id
        )
        assert_succeeded(response, statement_excerpt=statement)

    log("ok", f"applied {len(ALL_DDL)} workspace-KG DDLs", phase="kgddl")


# ---------------------------------------------------------------------------
# Phase 5 — Budget namespaces config table + 2 rows
# ---------------------------------------------------------------------------


def ensure_budget_namespaces(
    *, client: Any, cfg: LocalDeployConfig, warehouse_id: str
) -> None:
    log("step", "Phase 5 — budget namespaces", phase="budget")

    create_stmt = textwrap.dedent(
        f"""
        CREATE TABLE IF NOT EXISTS {cfg.catalog}.{cfg.schema}.budget_namespaces (
          namespace        STRING NOT NULL,
          ledger_table     STRING NOT NULL,
          enacted_at_ms    BIGINT NOT NULL,
          PRIMARY KEY (namespace) RELY
        )
        USING DELTA
        TBLPROPERTIES (
          'delta.minReaderVersion' = '3',
          'delta.minWriterVersion' = '7',
          'delta.feature.allowColumnDefaults' = 'supported'
        )
        COMMENT 'Per-SP BudgetGuard ledger isolation (docs/13-model-routing-and-budget.md §11.4)'
        """
    ).strip()
    log("info", f"applying {cfg.catalog}.{cfg.schema}.budget_namespaces DDL", phase="budget")
    response = execute_statement(client, statement=create_stmt, warehouse_id=warehouse_id)
    assert_succeeded(response, statement_excerpt=create_stmt)

    now_ms = int(time.time() * 1000)
    rows = [
        (
            "app",
            f"{cfg.catalog}.{cfg.schema}.budget_ledger_app",
        ),
        (
            "indexer",
            f"{cfg.catalog}.{cfg.schema}.budget_ledger_indexer",
        ),
    ]
    for ns, ledger in rows:
        merge_stmt = textwrap.dedent(
            f"""
            MERGE INTO {cfg.catalog}.{cfg.schema}.budget_namespaces AS t
            USING (SELECT '{ns}' AS namespace, '{ledger}' AS ledger_table, {now_ms} AS enacted_at_ms) AS s
            ON t.namespace = s.namespace
            WHEN NOT MATCHED THEN INSERT (namespace, ledger_table, enacted_at_ms)
                                  VALUES (s.namespace, s.ledger_table, s.enacted_at_ms)
            """
        ).strip()
        log("info", f"upsert namespace={ns!r}", phase="budget")
        response = execute_statement(
            client, statement=merge_stmt, warehouse_id=warehouse_id
        )
        assert_succeeded(response, statement_excerpt=merge_stmt)

    log("ok", "budget namespaces app + indexer ready", phase="budget")


# ---------------------------------------------------------------------------
# Phase 6 — UC grants
# ---------------------------------------------------------------------------


def ensure_grants(
    *,
    client: Any,
    cfg: LocalDeployConfig,
    warehouse_id: str,
    app_principal: str,
    indexer_principal: str,
) -> None:
    log("step", "Phase 6 — UC grants", phase="grant")

    statements = [
        # Catalog-level: app SP needs USE_CATALOG to read; indexer SP owns the
        # schema (set in Phase 3) so doesn't need CREATE_SCHEMA.
        f"GRANT USE_CATALOG ON CATALOG {cfg.catalog} TO `{app_principal}`",
        f"GRANT USE_CATALOG ON CATALOG {cfg.catalog} TO `{indexer_principal}`",
        # Schema-level: app SP READ-only (SELECT only — N180 pre-flight enforces);
        # indexer SP is the OWNER (set in Phase 3) so all writes are implicit.
        f"GRANT USE_SCHEMA, SELECT ON SCHEMA {cfg.catalog}.{cfg.schema} TO `{app_principal}`",
        # Volume: indexer writes inter-task JSON; app SP doesn't need access.
        f"GRANT WRITE_VOLUME, READ_VOLUME ON VOLUME {cfg.catalog}.{cfg.schema}.{cfg.state_volume_name} TO `{indexer_principal}`",
    ]
    for stmt in statements:
        log("info", stmt, phase="grant")
        response = execute_statement(client, statement=stmt, warehouse_id=warehouse_id)
        assert_succeeded(response, statement_excerpt=stmt)

    log("ok", "grants applied", phase="grant")


# ---------------------------------------------------------------------------
# Phase 7 — Vector Search endpoint + 3 indexes
# ---------------------------------------------------------------------------


_DIRECT_VS_INDEX_NAME = "entity_index"
_DIRECT_VS_EMBEDDING_DIM = 1024


def ensure_vector_search(*, client: Any, cfg: LocalDeployConfig) -> None:
    log("step", "Phase 7 — Vector Search endpoint + indexes", phase="vs")

    if not cfg.auto_provision_vs:
        log("info", "skipping (BV_LOCAL_DEPLOY_AUTO_PROVISION_VS=false)", phase="vs")
        return

    from databricks.sdk.service.vectorsearch import (  # noqa: PLC0415
        EndpointType,
    )

    existing_endpoint = None
    try:
        for ep in client.vector_search_endpoints.list_endpoints():
            if getattr(ep, "name", None) == cfg.vs_endpoint:
                existing_endpoint = ep
                break
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"✗ Failed to list VS endpoints: {exc!r}. Verify your token has"
            f" `vectorsearch:endpoints.list` permission."
        ) from exc

    if existing_endpoint is None:
        log("info", f"creating VS endpoint {cfg.vs_endpoint!r} (this can take ~10 min)", phase="vs")
        client.vector_search_endpoints.create_endpoint(
            name=cfg.vs_endpoint,
            endpoint_type=EndpointType.STANDARD,
        )
    else:
        log("ok", f"VS endpoint {cfg.vs_endpoint!r} already exists", phase="vs")

    def _is_online() -> bool:
        ep = client.vector_search_endpoints.get_endpoint(endpoint_name=cfg.vs_endpoint)
        state_obj = getattr(ep, "endpoint_status", None) or getattr(ep, "state", None)
        state = getattr(state_obj, "state", state_obj)
        state_value = getattr(state, "value", state)
        return str(state_value).upper() in {"ONLINE", "READY"}

    online = poll_until(
        description=f"VS endpoint {cfg.vs_endpoint!r} ONLINE",
        predicate=_is_online,
        timeout_sec=cfg.vs_endpoint_timeout_sec,
        interval_sec=15.0,
        phase="vs",
    )
    if not online:
        raise SystemExit(
            f"✗ VS endpoint {cfg.vs_endpoint!r} did not reach ONLINE within"
            f" {cfg.vs_endpoint_timeout_sec}s. Re-run the script — endpoint"
            f" creation continues asynchronously and will be picked up next"
            f" pass."
        )

    log("ok", f"VS endpoint {cfg.vs_endpoint!r} ONLINE", phase="vs")

    # One Direct Access index. The indexer owns embedding creation and
    # incrementally upserts vectors after Delta persistence succeeds; Lakebase
    # is only the post-promote UI read replica, not the retrieval substrate.
    from databricks.sdk.service.vectorsearch import (  # noqa: PLC0415
        DirectAccessVectorIndexSpec,
        EmbeddingVectorColumn,
        VectorIndexType,
    )

    full_name = f"{cfg.catalog}.{cfg.schema}.{_DIRECT_VS_INDEX_NAME}"
    try:
        client.vector_search_indexes.get_index(index_name=full_name)
        log("ok", f"VS index {_DIRECT_VS_INDEX_NAME!r} already exists", phase="vs")
        return
    except Exception:  # noqa: BLE001 — "not found" path
        pass

    schema_json = json.dumps(
        {
            "id": "string",
            "embedding": "array<float>",
            "entity_id": "string",
            "entity_kind": "string",
            "snapshot_id": "string",
            "meta_skill_id": "string",
            "top_order_id": "string",
            "chunk_text": "string",
            "source_url": "string",
        },
        separators=(",", ":"),
    )

    log("info", f"creating direct-access VS index {_DIRECT_VS_INDEX_NAME!r}", phase="vs")
    try:
        client.vector_search_indexes.create_index(
            name=full_name,
            endpoint_name=cfg.vs_endpoint,
            primary_key="id",
            index_type=VectorIndexType.DIRECT_ACCESS,
            direct_access_index_spec=DirectAccessVectorIndexSpec(
                embedding_vector_columns=[
                    EmbeddingVectorColumn(
                        name="embedding",
                        embedding_dimension=_DIRECT_VS_EMBEDDING_DIM,
                    )
                ],
                schema_json=schema_json,
            )
        )
        log("ok", f"VS index {_DIRECT_VS_INDEX_NAME!r} created", phase="vs")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"✗ Failed to create direct-access VS index {full_name!r}: {exc!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Doctor mode (read-only diagnostics)
# ---------------------------------------------------------------------------


def doctor(*, client: Any, cfg: LocalDeployConfig) -> int:
    """Print a green/red checklist of every prerequisite without
    creating anything. Exits 0 when everything is ready."""

    log("step", "doctor — read-only diagnostics", phase="doctor")

    issues: list[str] = []

    # 1. SPs
    sp_state = {cfg.indexer_sp_name: False, cfg.app_sp_name: False}
    for sp in client.service_principals.list():
        display = getattr(sp, "display_name", None)
        if display in sp_state:
            sp_state[display] = True
    for name, present in sp_state.items():
        if present:
            log("ok", f"SP {name!r} present", phase="doctor")
        else:
            log("fail", f"SP {name!r} missing", phase="doctor")
            issues.append(f"missing SP: {name}")

    # 2. Warehouse
    wh_id = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
    )
    if wh_id:
        log("ok", f"warehouse env-resolved (id={wh_id})", phase="doctor")
    else:
        named = [
            wh
            for wh in client.warehouses.list()
            if getattr(wh, "name", None) == _WAREHOUSE_NAME
        ]
        if named:
            log("ok", f"warehouse {_WAREHOUSE_NAME!r} present", phase="doctor")
        else:
            log("fail", "no warehouse resolved", phase="doctor")
            issues.append("no SQL warehouse")

    # 3. VS endpoint
    try:
        ep = client.vector_search_endpoints.get_endpoint(endpoint_name=cfg.vs_endpoint)
        state_obj = getattr(ep, "endpoint_status", None) or getattr(ep, "state", None)
        state = getattr(state_obj, "state", state_obj)
        state_value = str(getattr(state, "value", state)).upper()
        if state_value in {"ONLINE", "READY"}:
            log("ok", f"VS endpoint {cfg.vs_endpoint!r} ONLINE", phase="doctor")
        else:
            log("warn", f"VS endpoint {cfg.vs_endpoint!r} state={state_value}", phase="doctor")
    except Exception:  # noqa: BLE001
        log("fail", f"VS endpoint {cfg.vs_endpoint!r} not found", phase="doctor")
        issues.append(f"VS endpoint missing: {cfg.vs_endpoint}")

    if issues:
        log("fail", f"doctor: {len(issues)} issue(s) to fix", phase="doctor")
        for issue in issues:
            log("fail", f"  - {issue}", phase="doctor")
        return 1

    log("ok", "doctor: all clear", phase="doctor")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_ALL_PHASES = (
    "sp",
    "whse",
    "uc",
    "ddl",
    "budget",
    "grant",
    "vs",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Read-only check; print missing prerequisites and exit non-zero on any miss.",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=_ALL_PHASES,
        help="Skip a phase by id. Repeatable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    load_dotenv(_REPO_ROOT / ".env")
    log_path = os.environ.get("BV_LOCAL_DEPLOY_LOG_PATH", "./local_deploy.log")
    configure_log_file(log_path)

    cfg = LocalDeployConfig.from_env()
    log(
        "step",
        f"local_deploy/provision_workspace.py — host={cfg.databricks_host}"
        f" catalog={cfg.catalog} schema={cfg.schema} vs_endpoint={cfg.vs_endpoint}",
        phase="boot",
    )

    dry_run = env_bool("BV_DRY_RUN", False)
    if dry_run:
        log(
            "warn",
            "BV_DRY_RUN=true — inspection mode; no workspace writes will be made",
            phase="boot",
        )
        if not args.doctor:
            log(
                "ok",
                "provision skipped; set BV_DRY_RUN=false for real workspace setup",
                phase="boot",
            )
            return 0

    client = workspace_client(cfg)

    if args.doctor:
        return doctor(client=client, cfg=cfg)

    skip = set(args.skip)

    sps_by_name: dict[str, str] = {}
    if "sp" not in skip:
        sps_by_name = ensure_service_principals(client=client, cfg=cfg)
    app_principal = sps_by_name.get(cfg.app_sp_name, cfg.app_sp_name)
    indexer_principal = sps_by_name.get(cfg.indexer_sp_name, cfg.indexer_sp_name)

    warehouse_id = ""
    if "whse" not in skip:
        warehouse_id = ensure_warehouse(client=client, cfg=cfg)

    if "uc" not in skip:
        ensure_uc_catalog_schema(
            client=client,
            cfg=cfg,
            warehouse_id=warehouse_id,
            indexer_principal=indexer_principal,
        )
    if "ddl" not in skip:
        ensure_capability_graph_ddl(
            client=client, cfg=cfg, warehouse_id=warehouse_id
        )
        ensure_workspace_kg_ddl(
            client=client, cfg=cfg, warehouse_id=warehouse_id
        )
    if "budget" not in skip:
        ensure_budget_namespaces(
            client=client, cfg=cfg, warehouse_id=warehouse_id
        )
    if "grant" not in skip:
        ensure_grants(
            client=client,
            cfg=cfg,
            warehouse_id=warehouse_id,
            app_principal=app_principal,
            indexer_principal=indexer_principal,
        )

    if "vs" not in skip:
        ensure_vector_search(client=client, cfg=cfg)

    log("ok", "provision_workspace: all phases complete", phase="boot")
    log(
        "info",
        f"  SP application_ids: {sps_by_name}",
        phase="boot",
    )
    log("info", f"  warehouse_id={warehouse_id}", phase="boot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
