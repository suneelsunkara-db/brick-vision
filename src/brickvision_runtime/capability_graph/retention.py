"""Indexer task 13 — 30-day retention sweep on the capability graph
(per §23.3.6).

Scans ``<BV_CATALOG>.<BV_SCHEMA>.corpus_snapshots`` for snapshots older
than the retention window AND not currently the active snapshot,
marking them ``deactivated_at_ms = now_ms``. Also scans
``<BV_CATALOG>.<BV_SCHEMA>.embedding_cache`` for rows whose
``last_used_at_ms`` falls outside the cache TTL, deleting them
outright (the cache is regenerable). Finally, for each deactivated
snapshot the function attempts to remove the matching staging
artifact directory under
``/Volumes/<catalog>/<schema>/<state_volume>/runs/<run_id>/`` (where
``<state_volume>`` resolves from ``BV_INDEXER_STATE_VOLUME``;
default ``indexer-state``) so the UC Volume doesn't accumulate
per-run JSON debris.

Why retention is its own module
================================

Per §23.3.6:
  * **Snapshots** are kept for 30 days post-deactivation as a
    rollback safety window — ``brickvision indexer rollback`` calls
    :mod:`promote` with the prior snapshot's id, so the snapshot's
    typed rows must remain queryable. After the 30-day window, we
    mark the row deactivated but leave the data: physical deletion
    is a separate manual operation (typically `OPTIMIZE` + `VACUUM`
    on a schedule), not a retention concern.
  * **Embedding-cache** rows are pure memoization — losing one just
    triggers a re-embed on the next refresh that touches the same
    content_hash. They have a 30-day TTL based on
    ``last_used_at_ms`` (which :mod:`embed` bumps on every cache
    hit), so a hot row stays cached forever.
  * **Staging volume directories** are write-only artifacts of the
    indexer Job's inter-task channel; once a snapshot has been
    deactivated they are pure waste and we ask UC Volumes to delete
    them via :mod:`databricks.sdk` ``files.delete_directory``.

Why deactivation is logical, not physical
==========================================

§23.4.2 invariant: ``corpus_snapshots`` is append-only
(``delta.appendOnly = true``). Mutating a row's ``deactivated_at_ms``
column is the ONE allowed update (enforced via Delta MERGE INTO with
a CHECK that only ``deactivated_at_ms`` and ``signature`` are
mutated). Physical deletion would require setting
``delta.appendOnly = false`` workspace-wide, which we explicitly
refuse (§23.4.2 invariant 4).

Discipline rule 15 (N189) — production-only retention
=====================================================

This module previously declared a ``LifecycleStore(Protocol)`` and
accepted a ``store`` parameter so offline tests could stub the
read+write side with in-memory data. Per [`docs/01-overview.md`](
../../../../docs/01-overview.md) §0 +
[`docs/10-generation-philosophy.md`](
../../../../docs/10-generation-philosophy.md) §8.6 that Protocol seam
was retired. The production code path now uses
:class:`databricks.sdk.WorkspaceClient` Statement Execution against
``BV_INDEXER_WAREHOUSE_ID`` for both the SELECT/UPDATE/DELETE row
operations AND the UC Volume ``files`` API for staging-directory
removal. The ``BV_DRY_RUN=true`` env-gate (per
[`docs/19-local-development.md`](
../../../../docs/19-local-development.md) §15.2.1) short-circuits
every external call: SQL statements + UC Volume paths are flushed
into ``tests/fixtures/capability_graph/last_retention_payload.json``
(override via ``BV_DRY_RUN_RETENTION_LOG``) and the read side returns
the canned eligibility lists from the same file's
``["seed"]`` block.

Reason codes
============

Per §23.3.6:
  * :data:`ReasonCode.CAPABILITY_GRAPH_RETENTION_DEACTIVATE_FAILED` —
    per-snapshot deactivation failure; sibling snapshots continue.
    Surfaced in :attr:`RetentionResult.errors`.
  * :data:`ReasonCode.CAPABILITY_GRAPH_RETENTION_CACHE_GC_FAILED` —
    embedding-cache deletion failure; non-fatal (the cache will
    naturally re-evict on the next refresh).
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Defaults (per §23.3.6)
# ---------------------------------------------------------------------------


_DEFAULT_RETENTION_DAYS: int = 30
_DEFAULT_EMBEDDING_TTL_DAYS: int = 30
_MS_PER_DAY: int = 86_400_000


_DEFAULT_DRY_RUN_LOG = "tests/fixtures/capability_graph/last_retention_payload.json"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionError:
    """One per-operation failure (sibling operations continue)."""

    operation: str
    """One of: ``"list_snapshots_eligible_for_deactivation"``,
    ``"deactivate_snapshot"``, ``"list_cold_embedding_cache_rows"``,
    ``"delete_embedding_cache_rows"``, ``"delete_staging_directory"``."""

    target: str  # snapshot_id, comma-joined content_hashes, or volume path
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionResult:
    """Aggregate output of one ``run_retention`` invocation."""

    now_ms: int
    active_snapshot_id: str
    snapshots_deactivated: int
    snapshots_failed: int
    embedding_cache_rows_deleted: int
    embedding_cache_failed: int
    staging_directories_deleted: int = 0
    staging_directories_failed: int = 0
    errors: tuple[RetentionError, ...] = ()
    started_at_ms: int = 0
    completed_at_ms: int = 0
    duration_ms: int = 0
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Production helpers — Statement Execution + UC Volume files API
# ---------------------------------------------------------------------------


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "false").lower() in ("1", "true", "yes")


def _resolve_dry_run_log_path() -> Path:
    raw = os.environ.get("BV_DRY_RUN_RETENTION_LOG", "").strip()
    return Path(raw) if raw else Path(_DEFAULT_DRY_RUN_LOG)


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


def _resolve_state_volume() -> str:
    """Return the indexer-state UC Volume name (per ``BV_INDEXER_STATE_VOLUME``).

    Default ``"indexer-state"``. Mirrors the dispatcher helper of the
    same name in :mod:`brickvision_runtime.databricks_jobs.run_capability_indexer`.
    """

    return os.environ.get("BV_INDEXER_STATE_VOLUME", "indexer-state").strip() or "indexer-state"


def _resolve_warehouse_id() -> str:
    warehouse_id = os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
    if not warehouse_id:
        raise RuntimeError(
            "BV_INDEXER_WAREHOUSE_ID env var is required for retention"
            " sweeps (Statement Execution needs a serverless SQL warehouse"
            " target)"
        )
    return warehouse_id


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _qualified(catalog: str, table: str) -> str:
    return f"{catalog}.{_resolve_schema()}.{table}"


def _read_canned_seed() -> dict[str, Any]:
    """Read the optional ``["seed"]`` block from the dry-run log."""

    target = _resolve_dry_run_log_path()
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    seed = payload.get("seed")
    return seed if isinstance(seed, dict) else {}


def _execute_statement(*, statement: str) -> Any:
    """Run a single statement and return its (possibly None) result."""

    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

    warehouse_id = _resolve_warehouse_id()
    client = WorkspaceClient()
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(
            f"Statement Execution returned state={state}; error={msg}"
        )
    return response.result


def _list_eligible_snapshots(
    *,
    catalog: str,
    now_ms: int,
    retention_window_ms: int,
    active_snapshot_id: str,
) -> Sequence[str]:
    cutoff = now_ms - retention_window_ms
    statement = (
        f"SELECT snapshot_id, run_id"
        f" FROM {_qualified(catalog, 'corpus_snapshots')}"
        f" WHERE deactivated_at_ms IS NULL"
        f" AND promoted_at_ms IS NOT NULL"
        f" AND promoted_at_ms < {cutoff}"
        f" AND snapshot_id <> {_sql_string_literal(active_snapshot_id)}"
    )
    result = _execute_statement(statement=statement)
    rows = getattr(result, "data_array", None) or []
    return tuple(str(r[0]) for r in rows if r)


def _list_cold_embedding_hashes(
    *, catalog: str, now_ms: int, ttl_ms: int,
) -> Sequence[str]:
    cutoff = now_ms - ttl_ms
    statement = (
        f"SELECT content_hash"
        f" FROM {_qualified(catalog, 'embedding_cache')}"
        f" WHERE last_used_at_ms < {cutoff}"
    )
    result = _execute_statement(statement=statement)
    rows = getattr(result, "data_array", None) or []
    return tuple(str(r[0]) for r in rows if r)


def _list_staging_directories(
    *,
    catalog: str,
    eligible_snapshot_ids: Sequence[str],
) -> Sequence[str]:
    """Resolve UC Volume staging directories for the given snapshots.

    Each ``corpus_snapshots`` row carries the ``run_id`` that wrote
    its staging artifacts; we rebuild the canonical
    ``/Volumes/<catalog>/<schema>/<state_volume>/runs/<run_id>`` path
    (where ``<state_volume>`` resolves from ``BV_INDEXER_STATE_VOLUME``;
    default ``indexer-state``) so the UC Volume sweep can
    ``files.delete_directory`` them.
    """

    if not eligible_snapshot_ids:
        return ()
    quoted = ", ".join(_sql_string_literal(sid) for sid in eligible_snapshot_ids)
    statement = (
        f"SELECT snapshot_id, refresh_plan_id"
        f" FROM {_qualified(catalog, 'corpus_snapshots')}"
        f" WHERE snapshot_id IN ({quoted})"
    )
    result = _execute_statement(statement=statement)
    rows = getattr(result, "data_array", None) or []
    state_volume = _resolve_state_volume()
    schema = _resolve_schema()
    paths: list[str] = []
    for r in rows:
        if not r or len(r) < 2:
            continue
        plan_id = str(r[1])
        # plan_id format: ``rp_<run_id>`` (see run_capability_indexer.run_plan)
        run_id = plan_id[3:] if plan_id.startswith("rp_") else plan_id
        paths.append(f"/Volumes/{catalog}/{schema}/{state_volume}/runs/{run_id}")
    return tuple(paths)


def _deactivate_snapshot(
    *, catalog: str, snapshot_id: str, deactivated_at_ms: int,
) -> None:
    statement = (
        f"UPDATE {_qualified(catalog, 'corpus_snapshots')}"
        f" SET deactivated_at_ms = {deactivated_at_ms}"
        f" WHERE snapshot_id = {_sql_string_literal(snapshot_id)}"
    )
    _execute_statement(statement=statement)


def _delete_embedding_cache_rows(
    *, catalog: str, content_hashes: Sequence[str],
) -> int:
    if not content_hashes:
        return 0
    quoted = ", ".join(_sql_string_literal(h) for h in content_hashes)
    statement = (
        f"DELETE FROM {_qualified(catalog, 'embedding_cache')}"
        f" WHERE content_hash IN ({quoted})"
    )
    _execute_statement(statement=statement)
    return len(content_hashes)


def _delete_staging_directory(*, volume_path: str) -> None:
    """Recursively delete a UC Volume directory."""

    from databricks.sdk import WorkspaceClient  # noqa: PLC0415

    client = WorkspaceClient()
    client.files.delete_directory(directory_path=volume_path)


# ---------------------------------------------------------------------------
# Dry-run log writer
# ---------------------------------------------------------------------------


def _flush_dry_run_log(
    *,
    catalog: str,
    now_ms: int,
    active_snapshot_id: str,
    statements: list[str],
    deleted_volume_paths: list[str],
    eligible_snapshots: Sequence[str],
    cold_hashes: Sequence[str],
) -> None:
    target = _resolve_dry_run_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "catalog": catalog,
                "now_ms": now_ms,
                "active_snapshot_id": active_snapshot_id,
                "eligible_snapshots": list(eligible_snapshots),
                "cold_embedding_hashes": list(cold_hashes),
                "statements": list(statements),
                "deleted_volume_paths": list(deleted_volume_paths),
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def run_retention(
    *,
    now_ms: int,
    active_snapshot_id: str,
    catalog: str = "brickvision",
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    embedding_ttl_days: int = _DEFAULT_EMBEDDING_TTL_DAYS,
    started_at_ms: int,
    completed_at_ms: int | None = None,
) -> RetentionResult:
    """Run the daily retention sweep.

    Parameters
    ----------
    now_ms : int
        Wall-clock for the sweep; eligibility is computed relative
        to this. The indexer Job's ``retention`` task passes
        ``time.time() * 1000`` here.
    active_snapshot_id : str
        Read from ``<BV_CATALOG>.<BV_SCHEMA>.active_snapshot_id`` BEFORE
        invoking this function. Excluded from the deactivation list
        (we never deactivate the currently-serving snapshot, even if
        it's older than the window — that's a strict invariant).
    catalog : str, default ``"brickvision"``
        UC catalog hosting the ``capability_graph`` schema and the
        staging volume.
    retention_days, embedding_ttl_days : int
        Defaults are 30/30 per §23.3.6; partner deploys override
        via the indexer Job task config.
    started_at_ms, completed_at_ms : int
        For telemetry.

    Returns
    -------
    RetentionResult
        Counts + errors. The indexer Job's ``retention`` task treats
        any per-operation failure as a non-fatal warning (the
        snapshot/cache will be retried on the next daily sweep) but
        still surfaces the count in the SLO logs. The :attr:`dry_run`
        flag echoes whether ``BV_DRY_RUN`` was active.

    Order of operations
    -------------------
    Snapshots first, then embedding cache, then staging-volume
    cleanup. Ordering matters operationally: if Delta-side ops fail,
    the staging cleanup is the lower-priority surface and runs only
    for snapshots that were successfully deactivated.
    """

    if retention_days <= 0:
        raise ValueError(f"retention_days must be > 0, got {retention_days}")
    if embedding_ttl_days <= 0:
        raise ValueError(f"embedding_ttl_days must be > 0, got {embedding_ttl_days}")

    retention_window_ms = retention_days * _MS_PER_DAY
    ttl_ms = embedding_ttl_days * _MS_PER_DAY

    snapshots_deactivated = 0
    snapshots_failed = 0
    embedding_cache_rows_deleted = 0
    embedding_cache_failed = 0
    staging_directories_deleted = 0
    staging_directories_failed = 0
    errors: list[RetentionError] = []
    dry_run = _is_dry_run()
    seed = _read_canned_seed() if dry_run else {}
    rendered_statements: list[str] = []
    deleted_volume_paths: list[str] = []
    deactivated_snapshot_ids: list[str] = []

    # === Pass 1: snapshot deactivation ===
    eligible: Sequence[str] = ()
    try:
        if dry_run:
            eligible = tuple(seed.get("eligible_snapshots", []))
        else:
            eligible = _list_eligible_snapshots(
                catalog=catalog,
                now_ms=now_ms,
                retention_window_ms=retention_window_ms,
                active_snapshot_id=active_snapshot_id,
            )
    except Exception as exc:  # noqa: BLE001 — defensive
        errors.append(
            RetentionError(
                operation="list_snapshots_eligible_for_deactivation",
                target=active_snapshot_id,
                error_kind=type(exc).__name__,
                error_message=str(exc),
            )
        )
        eligible = ()

    for snapshot_id in eligible:
        # Defensive: never deactivate the active snapshot, even if
        # the catalog returned it.
        if snapshot_id == active_snapshot_id:
            continue
        try:
            if dry_run:
                rendered_statements.append(
                    f"UPDATE {_qualified(catalog, 'corpus_snapshots')}"
                    f" SET deactivated_at_ms = {now_ms}"
                    f" WHERE snapshot_id = {_sql_string_literal(snapshot_id)}"
                )
            else:
                _deactivate_snapshot(
                    catalog=catalog,
                    snapshot_id=snapshot_id,
                    deactivated_at_ms=now_ms,
                )
            snapshots_deactivated += 1
            deactivated_snapshot_ids.append(snapshot_id)
        except Exception as exc:  # noqa: BLE001 — defensive
            snapshots_failed += 1
            errors.append(
                RetentionError(
                    operation="deactivate_snapshot",
                    target=snapshot_id,
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                )
            )

    # === Pass 2: embedding cache GC ===
    cold_hashes: Sequence[str] = ()
    try:
        if dry_run:
            cold_hashes = tuple(seed.get("cold_embedding_hashes", []))
        else:
            cold_hashes = _list_cold_embedding_hashes(
                catalog=catalog, now_ms=now_ms, ttl_ms=ttl_ms,
            )
    except Exception as exc:  # noqa: BLE001 — defensive
        errors.append(
            RetentionError(
                operation="list_cold_embedding_cache_rows",
                target="",
                error_kind=type(exc).__name__,
                error_message=str(exc),
            )
        )
        cold_hashes = ()

    if cold_hashes:
        try:
            if dry_run:
                quoted = ", ".join(
                    _sql_string_literal(h) for h in cold_hashes
                )
                rendered_statements.append(
                    f"DELETE FROM {_qualified(catalog, 'embedding_cache')}"
                    f" WHERE content_hash IN ({quoted})"
                )
                embedding_cache_rows_deleted = len(cold_hashes)
            else:
                embedding_cache_rows_deleted = _delete_embedding_cache_rows(
                    catalog=catalog, content_hashes=cold_hashes,
                )
        except Exception as exc:  # noqa: BLE001 — defensive
            embedding_cache_failed = len(cold_hashes)
            errors.append(
                RetentionError(
                    operation="delete_embedding_cache_rows",
                    target=",".join(cold_hashes[:5])
                    + (
                        f" (+{len(cold_hashes) - 5} more)"
                        if len(cold_hashes) > 5
                        else ""
                    ),
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                )
            )

    # === Pass 3: staging-volume directory cleanup for deactivated snaps ===
    if deactivated_snapshot_ids:
        try:
            if dry_run:
                state_volume = _resolve_state_volume()
                schema = _resolve_schema()
                volume_paths = tuple(
                    f"/Volumes/{catalog}/{schema}/{state_volume}/runs/{sid[5:]}"
                    if sid.startswith("snap_")
                    else f"/Volumes/{catalog}/{schema}/{state_volume}/runs/{sid}"
                    for sid in deactivated_snapshot_ids
                )
            else:
                volume_paths = _list_staging_directories(
                    catalog=catalog,
                    eligible_snapshot_ids=deactivated_snapshot_ids,
                )
        except Exception as exc:  # noqa: BLE001 — defensive
            volume_paths = ()
            errors.append(
                RetentionError(
                    operation="list_staging_directories",
                    target=",".join(deactivated_snapshot_ids[:5]),
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                )
            )

        for volume_path in volume_paths:
            try:
                if dry_run:
                    deleted_volume_paths.append(volume_path)
                else:
                    _delete_staging_directory(volume_path=volume_path)
                staging_directories_deleted += 1
            except Exception as exc:  # noqa: BLE001 — defensive
                staging_directories_failed += 1
                errors.append(
                    RetentionError(
                        operation="delete_staging_directory",
                        target=volume_path,
                        error_kind=type(exc).__name__,
                        error_message=str(exc),
                    )
                )

    if dry_run:
        _flush_dry_run_log(
            catalog=catalog,
            now_ms=now_ms,
            active_snapshot_id=active_snapshot_id,
            statements=rendered_statements,
            deleted_volume_paths=deleted_volume_paths,
            eligible_snapshots=eligible,
            cold_hashes=cold_hashes,
        )

    end = completed_at_ms if completed_at_ms is not None else now_ms
    return RetentionResult(
        now_ms=now_ms,
        active_snapshot_id=active_snapshot_id,
        snapshots_deactivated=snapshots_deactivated,
        snapshots_failed=snapshots_failed,
        embedding_cache_rows_deleted=embedding_cache_rows_deleted,
        embedding_cache_failed=embedding_cache_failed,
        staging_directories_deleted=staging_directories_deleted,
        staging_directories_failed=staging_directories_failed,
        errors=tuple(errors),
        started_at_ms=started_at_ms,
        completed_at_ms=end,
        duration_ms=max(0, end - started_at_ms),
        dry_run=dry_run,
    )


__all__ = [
    "RetentionError",
    "RetentionResult",
    "run_retention",
]
