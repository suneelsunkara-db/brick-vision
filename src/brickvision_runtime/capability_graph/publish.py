"""v0.7.7 — Lakebase Autoscaling Synced-Tables publish.

Per docs/23-databricks-capability-graph.md §23.7 + the Lakebase
Autoscaling docs at:

  * https://docs.databricks.com/aws/en/oltp/projects/get-started
  * https://docs.databricks.com/aws/en/oltp/projects/sync-tables
  * https://docs.databricks.com/api/workspace/postgres/createsyncedtable

The promote task (T12) atomically flips ``active_snapshot_id`` so the
new corpus_snapshot becomes visible. T14 (publish) runs in parallel
with T13 (retention) and is responsible for propagating the just-
promoted Delta state to the Lakebase Postgres read substrate by
calling the Synced-Tables API for the 10 UI-readable tables:

  ============================  ===================================
  Delta source (UC)             Synced PK columns
  ============================  ===================================
  top_orders                    snapshot_id, top_order_id
  meta_skills                   snapshot_id, meta_skill_id
  extensions                    snapshot_id, extension_id
  entity_edges                  snapshot_id, src_id, dst_id, edge_kind
  source_provenance             snapshot_id, entity_id, source_kind
  corpus_snapshots              snapshot_id
  active_snapshot_id            singleton_key
  refresh_plan                  refresh_plan_id
  corpus_health                 recorded_at_ms, source_kind
  source_authority              schema_version, source_kind
  ============================  ===================================

The other 3 tables (``embedding_cache``, ``smoke_baseline``,
``docs_section_aliases``) stay Delta-only — they are indexer-internal
and never read from the UI hot path.

Architecture (per the docs we cited in the homework round)
==========================================================

* Synced tables live in the **same UC catalog/schema as the source
  Delta table** — no separate Lakebase-typed UC catalog. Per
  https://docs.databricks.com/aws/en/oltp/projects/sync-tables :
  "When you create a synced table, you get: 1. A synced table in
  Unity Catalog that references the sync pipeline. 2. A Postgres
  table in Lakebase. ... In Postgres, the Unity Catalog schema name
  becomes the Postgres schema name."

* So the synced table at ``BV_CATALOG.BV_SCHEMA.top_orders_synced``
  surfaces in Lakebase Postgres as ``BV_SCHEMA.top_orders_synced``
  — the Postgres schema auto-equals the UC schema (no separate
  ``BV_LAKEBASE_SCHEMA`` env var; the directive "use BV_CATALOG/
  BV_SCHEMA" is satisfied by Databricks's own UC->Postgres mapping).

* ``create_database_objects_if_missing=True`` on the spec auto-
  provisions the Postgres schema + table — no psycopg DDL needed.
  This is why the ``lakebase-publish`` Job environment requires only
  ``databricks-sdk``.

* The publish step is an **end-to-end gate**. If Lakebase is unhealthy,
  the Synced-Tables API rate-limits, or Postgres has not caught up to
  the just-promoted Delta snapshot before the wait timeout, the publish
  task fails. A successful indexer run therefore means both Delta and
  Lakebase read tables are on the same active snapshot.

Idempotency
===========

Calling ``create_synced_database_table`` for a synced table that
already exists raises ``ResourceAlreadyExists`` (or a 409 ``ALREADY_
EXISTS``). The first run of the indexer creates the synced table +
its underlying Lakeflow Spark Declarative Pipeline; subsequent runs
land in the "already exists" branch and trigger a refresh via the
pipeline's update API. We treat both code paths as success.

Lazy SDK import
===============

This module imports ``databricks.sdk`` only inside the function
that actually issues the call (``_call_create_synced_table``). The
dataclasses + helpers in the module body have no external
dependencies, so the module imports cleanly on a development
machine without ``databricks-sdk`` installed (mirrors
``vs_upsert.py``'s lazy-import pattern).

Test isolation
==============

There is intentionally no Protocol-typed "client" injection seam
(per discipline rule 15: protocols whose only concrete subclass is
a test mock are forbidden). Tests cover the function via:

  * ``dry_run=True`` — exercises the per-table-outcome assembly,
    duration accounting, and naming derivation without ever
    constructing or calling the SDK.
  * Workspace-side integration tests — exercise the real SDK path
    against an ephemeral Lakebase project.
"""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from typing import Any


# ---------------------------------------------------------------------------
# Constants — the 10 UI-readable tables we publish to Lakebase
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _SyncedTableConfig:
    """Per-source-table publish config.

    Attributes
    ----------
    source_table:
        Bare table name (e.g. ``"top_orders"``); the fully-qualified
        UC name is composed at call time from ``catalog`` + ``schema``.
    primary_key_columns:
        Tuple of column names that uniquely identify a row. Required
        by the Synced-Tables API; also drives Postgres PK constraints
        on the Lakebase side.
    timeseries_key:
        Optional partition / time-ordering hint. We use ``None`` for
        all 7 tables; ``snapshot_id`` is already in the PK for the
        snapshot-keyed tables.
    """

    source_table: str
    primary_key_columns: tuple[str, ...]
    timeseries_key: str | None = None


_DEFAULT_PUBLISHED_TABLES: tuple[_SyncedTableConfig, ...] = (
    _SyncedTableConfig(
        source_table="top_orders",
        primary_key_columns=("snapshot_id", "top_order_id"),
    ),
    _SyncedTableConfig(
        source_table="meta_skills",
        primary_key_columns=("snapshot_id", "meta_skill_id"),
    ),
    _SyncedTableConfig(
        source_table="extensions",
        primary_key_columns=("snapshot_id", "extension_id"),
    ),
    _SyncedTableConfig(
        source_table="entity_edges",
        primary_key_columns=(
            "snapshot_id", "src_id", "dst_id", "edge_kind",
        ),
    ),
    _SyncedTableConfig(
        source_table="source_provenance",
        primary_key_columns=(
            "snapshot_id", "entity_id", "source_kind", "ref", "content_hash",
        ),
    ),
    _SyncedTableConfig(
        source_table="corpus_snapshots",
        primary_key_columns=("snapshot_id",),
    ),
    _SyncedTableConfig(
        source_table="active_snapshot_id",
        primary_key_columns=("singleton_key",),
    ),
    _SyncedTableConfig(
        source_table="refresh_plan",
        primary_key_columns=("refresh_plan_id",),
    ),
    _SyncedTableConfig(
        source_table="corpus_health",
        primary_key_columns=("recorded_at_ms", "source_kind"),
    ),
    _SyncedTableConfig(
        source_table="source_authority",
        primary_key_columns=("schema_version", "source_kind"),
    ),
)


_WORKSPACE_KG_PUBLISHED_TABLES: tuple[_SyncedTableConfig, ...] = (
    _SyncedTableConfig(
        source_table="workspace_claims_current",
        primary_key_columns=("claim_id",),
    ),
)


_VALID_SYNC_MODES: frozenset[str] = frozenset({
    "snapshot", "triggered", "continuous",
})


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TablePublishOutcome:
    """Per-table outcome of a publish attempt.

    Attributes
    ----------
    source_table:
        Bare source table name (e.g. ``"top_orders"``).
    target_uc_name:
        Fully-qualified UC name of the synced table that was created
        / refreshed (e.g. ``"bv.brickvision.top_orders_synced"``).
    action:
        One of ``"created"``, ``"refreshed"``, ``"already_exists"``,
        ``"skipped_dry_run"``, ``"failed"``.
    duration_ms:
        How long the SDK call took.
    pipeline_id:
        The underlying Lakeflow pipeline ID that drives the sync, when
        the SDK returned it on create. ``None`` when the call did not
        surface one (refresh path or dry-run).
    error_kind / error_message:
        Populated only when ``action == "failed"``.
    """

    source_table: str
    target_uc_name: str
    action: str
    duration_ms: int
    pipeline_id: str | None = None
    error_kind: str | None = None
    error_message: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class PublishResult:
    """Aggregate result of a publish run.

    Attributes
    ----------
    snapshot_id:
        The snapshot that the just-promoted Delta state corresponds to.
    branch_resource_path:
        Lakebase branch full resource path, e.g.
        ``"projects/brickvision-capability-graph/branches/production"``.
    postgres_database:
        Lakebase Postgres database name (``BV_LAKEBASE_DATABASE``).
    sync_mode:
        ``"snapshot"`` / ``"triggered"`` / ``"continuous"``.
    outcomes:
        Per-table outcomes (length == len(published_tables)).
    tables_created:
        Count of synced tables created on this run.
    tables_refreshed:
        Count of synced tables that already existed and were refreshed.
    tables_failed:
        Count of synced tables whose publish call raised. Non-fatal at
        the Job level — Delta state is the source of truth.
    duration_ms:
        Wall-clock duration of the whole publish step.
    skipped_dry_run:
        True iff ``dry_run`` was set; no SDK calls were made.
    """

    snapshot_id: str
    branch_resource_path: str
    postgres_database: str
    sync_mode: str
    outcomes: tuple[TablePublishOutcome, ...]
    tables_created: int
    tables_refreshed: int
    tables_failed: int
    duration_ms: int
    skipped_dry_run: bool = False
    sync_verified: bool = False
    sync_wait_ms: int = 0
    synced_snapshot_id: str | None = None
    sync_errors: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _branch_resource_path(*, project_id: str, branch: str) -> str:
    """Compose the Lakebase branch resource path.

    Per the Autoscaling API guide
    (docs.databricks.com/aws/en/oltp/projects/api-usage), child
    resources are scoped to their parent project, e.g.
    ``projects/<project_id>/branches/<branch_id>``.
    """

    return f"projects/{project_id}/branches/{branch}"


def _is_already_exists(exc: BaseException) -> bool:
    """Heuristic: does this exception denote "synced table already exists"?

    The Databricks SDK raises ``ResourceAlreadyExists`` for 409 ALREADY_
    EXISTS responses. We test by class name + the well-known error
    message tokens so the check survives a minor SDK rev without us
    needing to import the exception class at top level.
    """

    name = type(exc).__name__
    if name in {"ResourceAlreadyExists", "AlreadyExists"}:
        return True
    msg = str(exc).lower()
    return "already exists" in msg or "already_exists" in msg


def _now_ms() -> int:
    """Wall-clock in ms — centralized for test override."""

    return int(time.time() * 1000)


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _safe_pg_ident(name: str) -> str:
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"unsafe Postgres identifier: {name!r}")
    return name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def publish_to_lakebase(
    *,
    snapshot_id: str,
    catalog: str,
    schema: str,
    project_id: str,
    branch: str,
    postgres_database: str,
    sync_mode: str,
    dry_run: bool = False,
    started_at_ms: int | None = None,
    published_tables: tuple[_SyncedTableConfig, ...] | None = None,
    wait_for_sync: bool = True,
    sync_timeout_seconds: int | None = None,
    sync_poll_interval_seconds: int | None = None,
    reset_existing: bool = False,
) -> PublishResult:
    """Publish the just-promoted Delta state to Lakebase.

    Idempotent: on first run, creates one synced table per source
    table (and the underlying Lakeflow Spark Declarative Pipeline);
    on subsequent runs, hits "already exists" and triggers a refresh
    on the existing pipeline.

    The Postgres schema where the synced tables land is the same as
    the UC schema (``schema``). The synced UC view lives at
    ``catalog.schema.<source>_synced`` and surfaces in Lakebase as
    ``schema.<source>_synced``.

    Parameters
    ----------
    snapshot_id:
        The just-promoted snapshot id (echoed into the result for
        observability + the corpus_health row downstream).
    catalog, schema:
        ``BV_CATALOG`` + ``BV_SCHEMA`` — both the source Delta tables
        AND the UC parent of the synced views live here.
    project_id, branch, postgres_database, sync_mode:
        ``BV_LAKEBASE_*`` env values.
    dry_run:
        When True, no SDK calls are made; the result has
        ``skipped_dry_run=True`` and per-table outcomes carry
        ``action="skipped_dry_run"``. Used when ``BV_DRY_RUN=true``
        and by smoke tests.
    started_at_ms:
        Optional override for the run's wall-clock start (lets the
        dispatcher align ``duration_ms`` with the dispatcher's
        timing). Defaults to ``time.time()``.
    published_tables:
        Override the closed list of tables to publish. Defaults to
        the 10 UI-readable tables (see :data:`_DEFAULT_PUBLISHED_TABLES`).

    Returns
    -------
    PublishResult
        Aggregate outcome with per-table details. Raises when any synced
        table publish call fails, or when Lakebase Postgres does not expose
        the promoted snapshot before the sync wait timeout.
    """

    if sync_mode.lower() not in _VALID_SYNC_MODES:
        raise ValueError(
            f"sync_mode must be one of {sorted(_VALID_SYNC_MODES)!r}; "
            f"got {sync_mode!r}",
        )
    sync_mode_norm = sync_mode.lower()

    if not project_id.strip():
        raise ValueError(
            "project_id is required (BV_LAKEBASE_PROJECT_ID env var)",
        )
    if not postgres_database.strip():
        raise ValueError(
            "postgres_database is required (BV_LAKEBASE_DATABASE env var)",
        )

    tables = published_tables or _DEFAULT_PUBLISHED_TABLES
    branch_path = _branch_resource_path(project_id=project_id, branch=branch)
    start = int(started_at_ms) if started_at_ms is not None else _now_ms()

    if dry_run:
        outcomes = tuple(
            TablePublishOutcome(
                source_table=t.source_table,
                target_uc_name=f"{catalog}.{schema}.{t.source_table}_synced",
                action="skipped_dry_run",
                duration_ms=0,
            )
            for t in tables
        )
        return PublishResult(
            snapshot_id=snapshot_id,
            branch_resource_path=branch_path,
            postgres_database=postgres_database,
            sync_mode=sync_mode_norm,
            outcomes=outcomes,
            tables_created=0,
            tables_refreshed=0,
            tables_failed=0,
            duration_ms=_now_ms() - start,
            skipped_dry_run=True,
        )

    workspace_postgres_api = _build_workspace_postgres_api()

    if reset_existing:
        _delete_existing_synced_tables(
            tables=tables,
            catalog=catalog,
            schema=schema,
            project_id=project_id,
            branch=branch,
            postgres_database=postgres_database,
            workspace_postgres_api=workspace_postgres_api,
        )

    outcomes_list: list[TablePublishOutcome] = []
    for cfg in tables:
        outcomes_list.append(
            _publish_one(
                cfg=cfg,
                catalog=catalog,
                schema=schema,
                branch_path=branch_path,
                postgres_database=postgres_database,
                sync_mode=sync_mode_norm,
                workspace_postgres_api=workspace_postgres_api,
            )
        )

    created = sum(1 for o in outcomes_list if o.action == "created")
    refreshed = sum(
        1 for o in outcomes_list
        if o.action in ("already_exists", "refreshed")
    )
    failed = sum(1 for o in outcomes_list if o.action == "failed")

    sync_verified = False
    sync_wait_ms = 0
    synced_snapshot_id: str | None = None
    sync_errors: tuple[str, ...] = ()
    if failed:
        sync_errors = tuple(
            f"{o.source_table}: {o.error_kind}: {o.error_message}"
            for o in outcomes_list
            if o.action == "failed"
        )
        raise RuntimeError(
            "Lakebase publish failed for "
            f"{failed} table(s): {list(sync_errors)!r}",
        )
    elif wait_for_sync:
        wait_started = _now_ms()
        verification = _wait_for_synced_tables(
            snapshot_id=snapshot_id,
            schema=schema,
            project_id=project_id,
            branch=branch,
            postgres_database=postgres_database,
            timeout_seconds=(
                sync_timeout_seconds
                if sync_timeout_seconds is not None
                else _env_int("BV_LAKEBASE_SYNC_WAIT_TIMEOUT_SECONDS", default=900)
            ),
            poll_interval_seconds=(
                sync_poll_interval_seconds
                if sync_poll_interval_seconds is not None
                else _env_int("BV_LAKEBASE_SYNC_POLL_INTERVAL_SECONDS", default=15)
            ),
        )
        sync_wait_ms = _now_ms() - wait_started
        sync_verified = bool(verification["verified"])
        synced_snapshot_id = verification["synced_snapshot_id"]
        sync_errors = tuple(verification["errors"])
        if not sync_verified:
            raise RuntimeError(
                "Lakebase synced tables did not catch up to promoted snapshot "
                f"{snapshot_id!r} within {sync_wait_ms}ms; "
                f"last_synced_snapshot={synced_snapshot_id!r}; "
                f"errors={list(sync_errors)!r}",
            )

    return PublishResult(
        snapshot_id=snapshot_id,
        branch_resource_path=branch_path,
        postgres_database=postgres_database,
        sync_mode=sync_mode_norm,
        outcomes=tuple(outcomes_list),
        tables_created=created,
        tables_refreshed=refreshed,
        tables_failed=failed,
        duration_ms=_now_ms() - start,
        skipped_dry_run=False,
        sync_verified=sync_verified,
        sync_wait_ms=sync_wait_ms,
        synced_snapshot_id=synced_snapshot_id,
        sync_errors=sync_errors,
    )


def publish_workspace_kg_to_lakebase(
    *,
    run_id: str,
    catalog: str,
    schema: str,
    project_id: str,
    branch: str,
    postgres_database: str,
    sync_mode: str,
    dry_run: bool = False,
    started_at_ms: int | None = None,
) -> PublishResult:
    """Publish Workspace KG current-state tables to Lakebase."""

    return publish_to_lakebase(
        snapshot_id=run_id,
        catalog=catalog,
        schema=schema,
        project_id=project_id,
        branch=branch,
        postgres_database=postgres_database,
        sync_mode=sync_mode,
        dry_run=dry_run,
        started_at_ms=started_at_ms,
        published_tables=_WORKSPACE_KG_PUBLISHED_TABLES,
        wait_for_sync=False,
    )


# ---------------------------------------------------------------------------
# Internal — single-table publish
# ---------------------------------------------------------------------------


def _delete_existing_synced_tables(
    *,
    tables: tuple[_SyncedTableConfig, ...],
    catalog: str,
    schema: str,
    project_id: str,
    branch: str,
    postgres_database: str,
    workspace_postgres_api: Any,
) -> None:
    """Delete old synced tables so a reset run starts from clean Lakebase state.

    Synced tables surface as UC ``TABLE_ONLINE_VIEW`` objects, and SQL
    ``DROP TABLE`` is forbidden for those securables. Use the Lakebase
    Synced Tables API as the owner of this lifecycle.
    """

    for cfg in tables:
        target_full = f"{catalog}.{schema}.{cfg.source_table}_synced"
        target_resource = f"synced_tables/{target_full}"
        try:
            print(f"[lakebase-sync] deleting synced table {target_full}", flush=True)
            operation = workspace_postgres_api.delete_synced_table(
                name=target_resource,
            )
            _wait_lro(operation)
        except BaseException as exc:  # noqa: BLE001
            if _is_not_found(exc):
                pass
            else:
                raise RuntimeError(
                    f"Failed to delete existing synced table {target_full!r}: {exc}",
                ) from exc
        _drop_postgres_synced_table(
            table_name=f"{cfg.source_table}_synced",
            schema=schema,
            project_id=project_id,
            branch=branch,
            postgres_database=postgres_database,
        )
        _wait_for_synced_table_deleted(target_resource)


def _wait_lro(operation: Any) -> None:
    wait = getattr(operation, "wait", None)
    if callable(wait):
        wait()


def _drop_postgres_synced_table(
    *,
    table_name: str,
    schema: str,
    project_id: str,
    branch: str,
    postgres_database: str,
) -> None:
    pg_schema = _safe_pg_ident(schema)
    pg_table = _safe_pg_ident(table_name)
    with _connect_lakebase_postgres(
        project_id=project_id,
        branch=branch,
        postgres_database=postgres_database,
    ) as conn:
        with conn.cursor() as cur:
            print(
                f"[lakebase-sync] dropping Postgres table {pg_schema}.{pg_table}",
                flush=True,
            )
            cur.execute(f"DROP TABLE IF EXISTS {pg_schema}.{pg_table}")


def _is_not_found(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {"NotFound", "ResourceDoesNotExist", "NotFoundError"}:
        return True
    msg = str(exc).lower()
    return "not found" in msg or "does not exist" in msg


def _wait_for_synced_table_deleted(target_resource: str) -> None:
    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]  # noqa: PLC0415

    client = WorkspaceClient()
    deadline = time.time() + _env_int(
        "BV_LAKEBASE_SYNC_DELETE_TIMEOUT_SECONDS", default=300,
    )
    while time.time() < deadline:
        try:
            client.postgres.get_synced_table(name=target_resource)
        except BaseException as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return
            raise
        time.sleep(5)
    raise RuntimeError(
        f"Timed out waiting for synced table delete: {target_resource!r}",
    )


def _publish_one(
    *,
    cfg: _SyncedTableConfig,
    catalog: str,
    schema: str,
    branch_path: str,
    postgres_database: str,
    sync_mode: str,
    workspace_postgres_api: Any,
) -> TablePublishOutcome:
    """Publish one source table; return a TablePublishOutcome."""

    source_full = f"{catalog}.{schema}.{cfg.source_table}"
    target_full = f"{catalog}.{schema}.{cfg.source_table}_synced"
    started_ms = _now_ms()

    spec = _build_spec(
        cfg=cfg,
        catalog=catalog,
        schema=schema,
        branch_path=branch_path,
        postgres_database=postgres_database,
        sync_mode=sync_mode,
        source_table_full_name=source_full,
    )
    table_obj = _build_table_obj(name=None, spec=spec)

    try:
        print(f"[lakebase-sync] creating synced table {target_full}", flush=True)
        response = workspace_postgres_api.create_synced_table(
            synced_table=table_obj, synced_table_id=target_full,
        )
        _wait_lro(response)
    except BaseException as exc:  # noqa: BLE001
        if _is_already_exists(exc):
            pipeline_id = _start_existing_synced_table_update(target_full)
            return TablePublishOutcome(
                source_table=cfg.source_table,
                target_uc_name=target_full,
                action="refreshed",
                duration_ms=_now_ms() - started_ms,
                pipeline_id=pipeline_id,
            )
        return TablePublishOutcome(
            source_table=cfg.source_table,
            target_uc_name=target_full,
            action="failed",
            duration_ms=_now_ms() - started_ms,
            error_kind=type(exc).__name__,
            error_message=str(exc) or traceback.format_exc(limit=2),
        )

    pipeline_id = _extract_pipeline_id(response)
    return TablePublishOutcome(
        source_table=cfg.source_table,
        target_uc_name=target_full,
        action="created",
        duration_ms=_now_ms() - started_ms,
        pipeline_id=pipeline_id,
    )


def _start_existing_synced_table_update(target_full: str) -> str | None:
    """Trigger the Lakeflow sync pipeline for an existing synced table."""

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]  # noqa: PLC0415

    client = WorkspaceClient()
    prefix = f"Synced table: {target_full} "
    for pipeline in client.pipelines.list_pipelines():
        name = getattr(pipeline, "name", "") or ""
        if name == target_full or name.startswith(prefix):
            pipeline_id = getattr(pipeline, "pipeline_id", None)
            if pipeline_id:
                client.pipelines.start_update(pipeline_id=pipeline_id)
                return str(pipeline_id)
    raise RuntimeError(f"Could not find Lakeflow sync pipeline for {target_full!r}")


def _wait_for_synced_tables(
    *,
    snapshot_id: str,
    schema: str,
    project_id: str,
    branch: str,
    postgres_database: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> dict[str, Any]:
    """Block until Lakebase Postgres exposes the promoted snapshot.

    The Synced Tables API call starts or attaches the Lakeflow sync pipeline,
    but the read substrate is not usable until Postgres has the new active
    pointer and the snapshot-keyed tables contain rows for that pointer.
    """

    if timeout_seconds <= 0:
        raise ValueError("sync timeout must be > 0 seconds")
    if poll_interval_seconds <= 0:
        raise ValueError("sync poll interval must be > 0 seconds")

    import psycopg  # type: ignore[import-not-found]  # noqa: PLC0415

    pg_schema = _safe_pg_ident(schema)
    deadline = time.time() + timeout_seconds
    last_snapshot: str | None = None
    last_errors: list[str] = []

    while True:
        last_errors = []
        try:
            with _connect_lakebase_postgres(
                project_id=project_id,
                branch=branch,
                postgres_database=postgres_database,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 10000")
                    cur.execute(
                        f"""
                        SELECT snapshot_id
                        FROM {pg_schema}.active_snapshot_id_synced
                        WHERE singleton_key = %s
                        """,
                        ("singleton",),
                    )
                    row = cur.fetchone()
                    last_snapshot = str(row[0]) if row else None
                    if last_snapshot != snapshot_id:
                        last_errors.append(
                            "active_snapshot_id_synced has "
                            f"{last_snapshot!r}, expected {snapshot_id!r}",
                        )
                    else:
                        expected_tables = (
                            "corpus_snapshots_synced",
                            "top_orders_synced",
                            "meta_skills_synced",
                            "extensions_synced",
                            "entity_edges_synced",
                            "source_provenance_synced",
                        )
                        for table in expected_tables:
                            cur.execute(
                                f"""
                                SELECT count(*)
                                FROM {pg_schema}.{table}
                                WHERE snapshot_id = %s
                                """,
                                (snapshot_id,),
                            )
                            count_row = cur.fetchone()
                            count = int(count_row[0]) if count_row else 0
                            if count <= 0:
                                last_errors.append(
                                    f"{table} has no rows for {snapshot_id!r}",
                                )
                        if not last_errors:
                            return {
                                "verified": True,
                                "synced_snapshot_id": last_snapshot,
                                "errors": [],
                            }
        except psycopg.Error as exc:
            last_errors.append(f"{type(exc).__name__}: {exc}")

        if time.time() >= deadline:
            return {
                "verified": False,
                "synced_snapshot_id": last_snapshot,
                "errors": last_errors,
            }
        time.sleep(poll_interval_seconds)


def _connect_lakebase_postgres(
    *,
    project_id: str,
    branch: str,
    postgres_database: str,
) -> Any:
    """Open a Lakebase Postgres connection using workspace OAuth."""

    import psycopg  # type: ignore[import-not-found]  # noqa: PLC0415
    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]  # noqa: PLC0415

    client = WorkspaceClient()
    branch_resource = _branch_resource_path(project_id=project_id, branch=branch)
    endpoint = list(client.postgres.list_endpoints(parent=branch_resource))[0]
    status = getattr(endpoint, "status", None)
    hosts = getattr(status, "hosts", None) if status is not None else None
    host = getattr(hosts, "host", "") if hosts is not None else ""
    if not host:
        raise RuntimeError(
            f"Lakebase branch {branch_resource!r} has no reachable Postgres host",
        )

    credential = client.postgres.generate_database_credential(
        endpoint=endpoint.name,
    )
    token = getattr(credential, "token", None) or getattr(
        credential, "credential", None,
    )
    if not token:
        raise RuntimeError("Lakebase credential response did not include a token")

    explicit_principal = (
        os.environ.get("PGUSER", "").strip()
        or os.environ.get("BV_LAKEBASE_PRINCIPAL", "").strip()
        or os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    )
    if explicit_principal:
        principal = explicit_principal
    else:
        me = client.current_user.me()
        principal = (me.user_name or me.display_name or "").strip()
    if not principal:
        raise RuntimeError("Could not resolve Lakebase Postgres principal")

    return psycopg.connect(
        host=host,
        port=5432,
        dbname=postgres_database,
        user=principal,
        password=token,
        sslmode="require",
        autocommit=True,
        connect_timeout=10,
    )


# ---------------------------------------------------------------------------
# SDK glue (lazy)
# ---------------------------------------------------------------------------


def _build_workspace_postgres_api() -> Any:  # noqa: ANN401
    """Construct the production ``WorkspaceClient.postgres`` namespace.

    Lazy import so this module loads on dev machines without
    ``databricks-sdk``. The Job's ``lakebase-publish`` environment
    pins ``databricks-sdk>=0.68``. Uses the Autoscaling Postgres API
    (``client.postgres``) — NOT the legacy ``client.database`` API.
    """

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]

    return WorkspaceClient().postgres


def _build_spec(
    *,
    cfg: _SyncedTableConfig,
    catalog: str,
    schema: str,
    branch_path: str,
    postgres_database: str,
    sync_mode: str,
    source_table_full_name: str,
) -> Any:  # noqa: ANN401
    """Build a SyncedTableSyncedTableSpec for the Autoscaling Postgres API."""

    from databricks.sdk.service.postgres import (  # type: ignore[import-not-found]
        NewPipelineSpec,
        SyncedTableSyncedTableSpec,
        SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy,
    )

    policy = {
        "snapshot": SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.SNAPSHOT,
        "triggered": SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.TRIGGERED,
        "continuous": SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.CONTINUOUS,
    }[sync_mode]

    return SyncedTableSyncedTableSpec(
        branch=branch_path,
        postgres_database=postgres_database,
        source_table_full_name=source_table_full_name,
        primary_key_columns=list(cfg.primary_key_columns),
        scheduling_policy=policy,
        create_database_objects_if_missing=True,
        timeseries_key=cfg.timeseries_key,
        new_pipeline_spec=NewPipelineSpec(
            storage_catalog=catalog,
            storage_schema=schema,
        ),
    )


def _build_table_obj(*, name: str | None, spec: Any) -> Any:  # noqa: ANN401
    """Build a SyncedTable for the Autoscaling Postgres API."""

    from databricks.sdk.service.postgres import SyncedTable  # type: ignore[import-not-found]

    return SyncedTable(name=name, spec=spec)


def _extract_pipeline_id(response: Any) -> str | None:  # noqa: ANN401
    """Best-effort extraction of the underlying Lakeflow pipeline_id.

    The ``create_synced_table`` response is a ``CreateSyncedTableOperation``
    (an LRO). We look for pipeline_id in the response metadata, the
    synced table status, or nested attributes.
    """

    if response is None:
        return None
    for attr in ("status", "data_synchronization_status", "metadata"):
        section = getattr(response, attr, None)
        if section is None:
            continue
        pid = getattr(section, "pipeline_id", None)
        if isinstance(pid, str) and pid:
            return pid
    # Try response directly if it's the SyncedTable result
    synced = getattr(response, "response", None) or response
    if hasattr(synced, "status"):
        pid = getattr(synced.status, "pipeline_id", None)
        if isinstance(pid, str) and pid:
            return pid
    return None


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


__all__ = [
    "PublishResult",
    "TablePublishOutcome",
    "publish_workspace_kg_to_lakebase",
    "publish_to_lakebase",
]
