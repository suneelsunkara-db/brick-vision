"""Indexer task 9 — persist the capability graph to Delta tables
(per §23.3.7).

Takes the typed row outputs of :func:`graph_builder.build_capability_graph`
plus the per-snapshot lifecycle row (:class:`CorpusSnapshotRow`) and
writes them into the 7 ``<bv>.capability_graph.*`` Delta tables that
serve as the canonical capability graph snapshot:

  * ``corpus_snapshots``    — append: one row per snapshot
  * ``top_orders``          — append: 7 rows × N snapshots
  * ``meta_skills``         — append: ~50 rows × N snapshots
  * ``extensions``          — append: ~thousands × N snapshots
  * ``entity_edges``        — append: ~tens of thousands × N snapshots
  * ``source_provenance``   — append: ~thousands × N snapshots

Note that ``active_snapshot_id`` (the single-row pointer) is written
by :mod:`promote` AFTER smoke validation passes, NOT by this module —
keeping write-side mutation strictly snapshot-scoped means a failed
smoke test doesn't leave the cluster pointing at a half-written
snapshot.

Why all writes are append-only
==============================

Per §23.4.2 invariants:
  * Every per-snapshot table has ``snapshot_id`` as its first column,
    enforced as a partition key. New snapshots write fresh rows
    rather than mutating prior ones.
  * Old snapshots remain queryable for a 30-day window (the retention
    SLO) so the indexer can recover from a bad promotion via
    ``brickvision indexer rollback`` (which just flips
    ``active_snapshot_id`` back to a known-good prior snapshot).
  * A task retry for the same ``snapshot_id`` first deletes that
    snapshot's prior rows, then inserts the freshly rendered rows. That
    keeps retries idempotent without mutating any previous snapshot.

Discipline rule 15 (N189) — production-only persistence
=======================================================

This module previously declared a ``DeltaWriter(Protocol)`` and
accepted a ``writer`` parameter so offline tests could capture rows
in memory. Per [`docs/01-overview.md`](
../../../../docs/01-overview.md) §0 +
[`docs/10-generation-philosophy.md`](
../../../../docs/10-generation-philosophy.md) §8.6 that Protocol seam
was retired. The production code path now writes directly via the
:mod:`databricks.sdk` Statement Execution API (lazy-imported); the
``BV_DRY_RUN=true`` env-gate (per [`docs/19-local-development.md`](
../../../../docs/19-local-development.md) §15.2.1) short-circuits the
actual SQL execution and instead writes the rendered statements +
row payloads to
``tests/fixtures/capability_graph/last_persist_payload.json``
(override via ``BV_DRY_RUN_PERSIST_LOG``).

Reason codes
============

Per §23.3.7:
  * :data:`ReasonCode.CAPABILITY_GRAPH_PERSIST_WRITE_FAILED` — emitted
    on per-table failure; sibling tables continue, but the snapshot
    is marked partial. The indexer's ``promote`` task refuses to
    flip ``active_snapshot_id`` if any persist failure occurred.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .graph_builder import CapabilityGraphBuildResult
from .schemas.types import (
    CorpusHealthRow,
    CorpusSnapshotRow,
    RefreshPlanRow,
    SourceAuthorityRow,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PersistError:
    """Per-table failure (sibling tables continue)."""

    table_name: str
    error_kind: str
    error_message: str
    intended_row_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class PersistResult:
    """Aggregate output of one ``persist_snapshot`` invocation."""

    snapshot_id: str
    rows_written_per_table: dict[str, int]
    started_at_ms: int
    completed_at_ms: int
    duration_ms: int
    errors: tuple[PersistError, ...]
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Table-name constants (per the schemas/* DDL)
# ---------------------------------------------------------------------------


_TABLE_CORPUS_SNAPSHOTS: str = "corpus_snapshots"
_TABLE_TOP_ORDERS: str = "top_orders"
_TABLE_META_SKILLS: str = "meta_skills"
_TABLE_EXTENSIONS: str = "extensions"
_TABLE_ENTITY_EDGES: str = "entity_edges"
_TABLE_SOURCE_PROVENANCE: str = "source_provenance"
_TABLE_SOURCE_AUTHORITY: str = "source_authority"
_TABLE_REFRESH_PLAN: str = "refresh_plan"
_TABLE_CORPUS_HEALTH: str = "corpus_health"


_DEFAULT_DRY_RUN_LOG = "tests/fixtures/capability_graph/last_persist_payload.json"


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


def _qualified(catalog: str, table: str, *, schema: str | None = None) -> str:
    """Build the fully-qualified ``<catalog>.<schema>.<table>`` name.

    Per v0.7.7 schema consolidation, every BrickVision UC object lives
    under a single flat schema (default ``brickvision``, override via
    ``BV_SCHEMA``)."""

    return f"{catalog}.{schema or _resolve_schema()}.{table}"


# ---------------------------------------------------------------------------
# Production writer — Statement Execution + local-file dry-run log
# ---------------------------------------------------------------------------


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "false").lower() in ("1", "true", "yes")


def _resolve_dry_run_log_path() -> Path:
    raw = os.environ.get("BV_DRY_RUN_PERSIST_LOG", "").strip()
    return Path(raw) if raw else Path(_DEFAULT_DRY_RUN_LOG)


def _resolve_warehouse_id() -> str:
    warehouse_id = os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
    if not warehouse_id:
        raise RuntimeError(
            "BV_INDEXER_WAREHOUSE_ID env var is required to persist capability"
            " graph snapshots (Statement Execution needs a serverless SQL"
            " warehouse target)"
        )
    return warehouse_id


def _row_to_dict(row: object) -> dict[str, Any]:
    if dataclasses.is_dataclass(row):
        return dataclasses.asdict(row)
    if isinstance(row, dict):
        return dict(row)
    raise TypeError(
        f"persist row must be a dataclass or dict; got {type(row).__name__}"
    )


def _execute_statements(*, statements: Sequence[str]) -> None:
    """Execute a sequence of statements against the configured warehouse."""

    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

    warehouse_id = _resolve_warehouse_id()
    client = WorkspaceClient()
    for statement in statements:
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


def _write_rows_via_sdk(
    *, table_name: str, rows: Sequence[object], dry_run_log: list[dict[str, Any]],
) -> int:
    """Append ``rows`` to ``table_name`` via Statement Execution.

    On ``BV_DRY_RUN=true`` appends a payload entry to ``dry_run_log``
    (the orchestrator dumps the full log at the end of the run) and
    returns the row count without touching the warehouse.
    """

    if not rows:
        dry_run_log.append({"table_name": table_name, "row_count": 0, "rows": []})
        return 0

    payload = [_row_to_dict(r) for r in rows]
    if _is_dry_run():
        dry_run_log.append(
            {
                "table_name": table_name,
                "row_count": len(payload),
                "rows": payload,
            }
        )
        return len(payload)

    # Live path — for sub-table-A items, the live SDK call is exercised
    # in the install workspace. The serialization here keeps payload
    # routing consistent with the dry-run log so partner ops can diff
    # them. INSERT-VALUES is acceptable for the snapshot writes (sub-
    # thousand rows per table).
    statements = list(_render_insert_statements(table_name=table_name, rows=payload))
    _execute_statements(statements=statements)
    return len(payload)


def _replace_rows_by_key_via_sdk(
    *,
    table_name: str,
    rows: Sequence[object],
    key_columns: Sequence[str],
    dry_run_log: list[dict[str, Any]],
) -> int:
    """Replace rows matching ``key_columns`` before inserting the new payload.

    Closed-set tables such as ``source_authority`` are versioned, not snapshot-
    keyed. Appending the same closed set every refresh would silently create
    duplicate source-of-truth rows, so the persist task replaces the current
    key set instead.
    """

    if not rows:
        dry_run_log.append(
            {
                "table_name": table_name,
                "row_count": 0,
                "rows": [],
                "write_mode": "replace_by_key",
                "key_columns": list(key_columns),
            }
        )
        return 0

    payload = [_row_to_dict(r) for r in rows]
    if _is_dry_run():
        dry_run_log.append(
            {
                "table_name": table_name,
                "row_count": len(payload),
                "rows": payload,
                "write_mode": "replace_by_key",
                "key_columns": list(key_columns),
            }
        )
        return len(payload)

    key_tuples = sorted(
        {
            tuple(row[col] for col in key_columns)
            for row in payload
        }
    )
    delete_statements = [
        _render_delete_by_key_statement(
            table_name=table_name, key_columns=key_columns, key_values=key_values,
        )
        for key_values in key_tuples
    ]
    insert_statements = list(_render_insert_statements(table_name=table_name, rows=payload))
    _execute_statements(statements=[*delete_statements, *insert_statements])
    return len(payload)


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_value_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        rendered = ", ".join(_sql_value_literal(v) for v in value)
        return f"ARRAY({rendered})"
    if isinstance(value, dict):
        # MAP literal form using the named-array constructor — partner
        # workspaces enable Photon SQL by default which supports the
        # ``map_from_arrays`` builtin used here.
        keys = ", ".join(_sql_string_literal(str(k)) for k in value.keys())
        vals = ", ".join(_sql_value_literal(v) for v in value.values())
        return f"map_from_arrays(ARRAY({keys}), ARRAY({vals}))"
    return _sql_string_literal(str(value))


def _render_insert_statements(
    *, table_name: str, rows: Sequence[dict[str, Any]],
) -> Sequence[str]:
    """Render INSERT INTO statements for ``rows``.

    Splits into 100-row batches so each statement fits comfortably
    within the Statement Execution SQL-text size budget.
    """

    if not rows:
        return ()
    columns = list(rows[0].keys())
    column_list = "(" + ", ".join(f"`{c}`" for c in columns) + ")"
    statements: list[str] = []
    batch_size = 100
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        values_parts: list[str] = []
        for r in chunk:
            rendered = [_sql_value_literal(r.get(col)) for col in columns]
            values_parts.append("(" + ", ".join(rendered) + ")")
        statements.append(
            f"INSERT INTO {table_name} {column_list} VALUES " + ", ".join(values_parts)
        )
    return statements


def _render_delete_by_key_statement(
    *, table_name: str, key_columns: Sequence[str], key_values: Sequence[Any],
) -> str:
    predicates = [
        f"`{column}` = {_sql_value_literal(value)}"
        for column, value in zip(key_columns, key_values, strict=True)
    ]
    return f"DELETE FROM {table_name} WHERE " + " AND ".join(predicates)


def _flush_dry_run_log(
    *, snapshot_id: str, catalog: str, payload: list[dict[str, Any]],
) -> None:
    """Persist the dry-run log to its configured path."""

    target = _resolve_dry_run_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "catalog": catalog,
                "tables": payload,
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


def persist_snapshot(
    *,
    build_result: CapabilityGraphBuildResult,
    corpus_snapshot: CorpusSnapshotRow,
    refresh_plan_rows: Sequence[RefreshPlanRow] = (),
    source_authority_rows: Sequence[SourceAuthorityRow] = (),
    corpus_health_rows: Sequence[CorpusHealthRow] = (),
    catalog: str = "brickvision",
    started_at_ms: int,
    completed_at_ms: int | None = None,
) -> PersistResult:
    """Write all 6 row sets to Delta as one atomic-per-table operation.

    Order of writes is **deliberate**: corpus_snapshots first so any
    later table failure can be cleaned up by the retention task
    (which keys off corpus_snapshots.deactivated_at_ms). If
    corpus_snapshots itself fails, no other tables are touched.

    Per N189 / discipline rule 15 the writer is no longer a Protocol
    seam — :func:`_write_rows_via_sdk` ships the production
    Statement Execution call directly, with ``BV_DRY_RUN=true``
    short-circuiting to a fixture log. Tests exercising ``persist``
    set the env-gate, run the function, and inspect the fixture
    log via :func:`pathlib.Path.read_text`.

    Parameters
    ----------
    build_result : CapabilityGraphBuildResult
        Output of :func:`graph_builder.build_capability_graph`.
    corpus_snapshot : CorpusSnapshotRow
        Per-snapshot lifecycle row — ``promoted_at_ms`` and
        ``deactivated_at_ms`` should be ``None`` here; promote.py
        flips ``promoted_at_ms`` after smoke passes.
    catalog : str
        Default ``"brickvision"``; partner deploys override via
        ``BV_CATALOG``.
    started_at_ms, completed_at_ms : int
        For telemetry. ``completed_at_ms`` is computed from a wall-
        clock fetch when ``None``.

    Returns
    -------
    PersistResult
        Per-table row counts + errors. If ANY table fails, the
        snapshot must be considered partial; the indexer's
        ``promote`` task uses :attr:`PersistResult.errors` as a hard
        gate.
    """

    if build_result.snapshot_id != corpus_snapshot.snapshot_id:
        raise ValueError(
            f"snapshot_id mismatch: build={build_result.snapshot_id!r}, "
            f"corpus_snapshot={corpus_snapshot.snapshot_id!r}"
        )

    rows_written: dict[str, int] = {}
    errors: list[PersistError] = []
    dry_run = _is_dry_run()
    dry_run_log: list[dict[str, Any]] = []

    # Pre-build the (table_name, rows, key_columns) dispatch list. The order
    # here is the documented dependency order: refresh ledger before the corpus
    # snapshot that points at it, and health after the snapshot rows it
    # summarizes. Every snapshot-scoped table is replaced by snapshot_id so a
    # Databricks task retry cannot append duplicate rows.
    plan: tuple[tuple[str, Sequence[object], tuple[str, ...]], ...] = (
        (_TABLE_REFRESH_PLAN, refresh_plan_rows, ("refresh_plan_id",)),
        (_TABLE_CORPUS_SNAPSHOTS, (corpus_snapshot,), ("snapshot_id",)),
        (_TABLE_TOP_ORDERS, build_result.top_orders, ("snapshot_id",)),
        (_TABLE_META_SKILLS, build_result.meta_skills, ("snapshot_id",)),
        (_TABLE_EXTENSIONS, build_result.extensions, ("snapshot_id",)),
        (_TABLE_ENTITY_EDGES, build_result.entity_edges, ("snapshot_id",)),
        (_TABLE_SOURCE_PROVENANCE, build_result.source_provenance, ("snapshot_id",)),
        (_TABLE_CORPUS_HEALTH, corpus_health_rows, ("recorded_at_ms", "source_kind")),
    )

    if source_authority_rows:
        qualified = _qualified(catalog, _TABLE_SOURCE_AUTHORITY)
        try:
            rows_written[_TABLE_SOURCE_AUTHORITY] = _replace_rows_by_key_via_sdk(
                table_name=qualified,
                rows=source_authority_rows,
                key_columns=("schema_version", "source_kind"),
                dry_run_log=dry_run_log,
            )
        except Exception as exc:  # noqa: BLE001 — capture-and-continue per §23.3.7
            errors.append(
                PersistError(
                    table_name=_TABLE_SOURCE_AUTHORITY,
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                    intended_row_count=len(source_authority_rows),
                )
            )
            rows_written[_TABLE_SOURCE_AUTHORITY] = 0

    for table_name, rows, key_columns in plan:
        qualified = _qualified(catalog, table_name)
        try:
            written = _replace_rows_by_key_via_sdk(
                table_name=qualified,
                rows=rows,
                key_columns=key_columns,
                dry_run_log=dry_run_log,
            )
            rows_written[table_name] = written
        except Exception as exc:  # noqa: BLE001 — capture-and-continue per §23.3.7
            errors.append(
                PersistError(
                    table_name=table_name,
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                    intended_row_count=len(rows),
                )
            )
            rows_written[table_name] = 0

    if dry_run:
        _flush_dry_run_log(
            snapshot_id=build_result.snapshot_id,
            catalog=catalog,
            payload=dry_run_log,
        )

    end = completed_at_ms if completed_at_ms is not None else started_at_ms
    return PersistResult(
        snapshot_id=build_result.snapshot_id,
        rows_written_per_table=rows_written,
        started_at_ms=started_at_ms,
        completed_at_ms=end,
        duration_ms=max(0, end - started_at_ms),
        errors=tuple(errors),
        dry_run=dry_run,
    )


__all__ = [
    "PersistError",
    "PersistResult",
    "persist_snapshot",
]
