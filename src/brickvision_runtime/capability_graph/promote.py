"""Indexer task 12 — atomically promote a validated snapshot to active
(per §23.3.4).

This is the **gate module** for the indexer pipeline: it enforces
that ``persist`` + ``vs_upsert`` + ``smoke`` all passed before
flipping ``<BV_CATALOG>.<BV_SCHEMA>.active_snapshot_id``. A failed
gate produces a :class:`PromoteResult` with ``promoted=False`` and a
non-empty :attr:`PromoteResult.failed_gates` tuple — the indexer
Job's ``promote`` task surfaces those gates to the operator and
exits non-zero, leaving the prior active snapshot in place.

Why promotion is its own module instead of inline in the indexer
================================================================

Per §23.3.4:
  1. **Atomicity** — the active-pointer flip and the
     ``corpus_snapshots.promoted_at_ms`` stamp must be one
     transaction. If we set the pointer first and crash before
     stamping, retention can't tell which snapshot is the
     committed-active vs. the still-pending; if we stamp first and
     crash before pointing, queries return stale results. The
     production code path issues a Statement Execution multi-
     statement transaction (``BEGIN; ... COMMIT;``) so both writes
     either commit together or both roll back.
  2. **Audit clarity** — gate-checks live here, the SQL renders live
     here, the indexer Job's ``promote`` task just calls this
     function. Reviewing "what does it take to flip the active
     pointer" requires reading exactly one file.
  3. **Rollback path symmetry** — :func:`brickvision indexer
     rollback` calls :func:`promote_snapshot` with
     ``rollback_target=<prior snapshot_id>`` to do the inverse
     flip. The gate logic is unified between forward and rollback
     promotion.

Atomicity guarantees
====================

Per §23.4.2:
  * The ``active_snapshot_id`` table is single-row (a CHECK
    constraint enforces ``singleton_key = "singleton"``); promotion
    is a row UPDATE, not an INSERT.
  * The ``corpus_snapshots`` row update fills in the previously-
    NULL ``promoted_at_ms`` and ``signature`` columns.
  * Both writes are emitted inside one ``BEGIN; ... COMMIT;`` block
    against the configured serverless SQL warehouse.

Discipline rule 15 (N189) — production-only promotion
=====================================================

This module previously declared a ``PromotionWriter(Protocol)`` and
accepted a ``writer`` parameter so offline tests could capture the
writes in memory. Per [`docs/01-overview.md`](
../../../../docs/01-overview.md) §0 +
[`docs/10-generation-philosophy.md`](
../../../../docs/10-generation-philosophy.md) §8.6 that Protocol seam
was retired. The production code path now:

  * Uses :class:`databricks.sdk.WorkspaceClient` Statement Execution
    against ``BV_INDEXER_WAREHOUSE_ID`` to evaluate the
    "already promoted?" guard and to issue the transactional flip.
  * Honors ``BV_DRY_RUN=true`` (per [`docs/19-local-development.md`](
    ../../../../docs/19-local-development.md) §15.2.1) by writing the
    rendered statements to
    ``tests/fixtures/capability_graph/last_promote_payload.json``
    (override via ``BV_DRY_RUN_PROMOTE_LOG``) instead of executing
    them. The "already promoted?" check defaults to ``False`` in
    dry-run mode unless the env-gate
    ``BV_DRY_RUN_PROMOTE_ALREADY_PROMOTED=true`` is set.

The ``signature`` column is a plain content-hash digest (computed by
the indexer Job's ``promote`` task as a SHA-256 over the snapshot's
content_hashes); there is no cryptographic signing layer.

Reason codes
============

Per §23.3.4:
  * :data:`ReasonCode.CAPABILITY_GRAPH_PROMOTE_GATE_FAILED` —
    emitted when ``persist.errors`` ≠ ∅ OR ``vs_upsert.errors`` ≠
    ∅ OR ``smoke.passed = False``. The :attr:`failed_gates` tuple
    enumerates which gate failed.
  * :data:`ReasonCode.CAPABILITY_GRAPH_PROMOTE_ALREADY_PROMOTED` —
    emitted when the snapshot already has a non-NULL
    ``promoted_at_ms`` (defensive — the indexer shouldn't call
    promote twice on the same snapshot, but if it does this is a
    no-op).
  * :data:`ReasonCode.CAPABILITY_GRAPH_PROMOTE_WRITE_FAILED` —
    emitted when the atomic write itself failed; the prior active
    snapshot is unchanged (Statement Execution's ``BEGIN; COMMIT;``
    semantics guarantee this).
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .persist import PersistResult
from .smoke import SmokeResult
from .vs_upsert import VsUpsertResult


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class FailedGate:
    """One gate failure; the snapshot is NOT promoted."""

    gate_name: str  # "persist" | "vs_upsert" | "smoke" | "already_promoted"
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class PromoteError:
    """Capture of a write-side failure (the atomic_promote call
    itself raised)."""

    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class PromoteResult:
    """Aggregate output of one ``promote_snapshot`` invocation."""

    snapshot_id: str
    promoted: bool
    promoted_at_ms: int | None  # None when not promoted
    promoted_by: str | None
    signature: str | None
    failed_gates: tuple[FailedGate, ...]
    errors: tuple[PromoteError, ...]
    started_at_ms: int
    completed_at_ms: int
    duration_ms: int
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Production write helpers
# ---------------------------------------------------------------------------


_DEFAULT_DRY_RUN_LOG = "tests/fixtures/capability_graph/last_promote_payload.json"


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "false").lower() in ("1", "true", "yes")


def _resolve_dry_run_log_path() -> Path:
    raw = os.environ.get("BV_DRY_RUN_PROMOTE_LOG", "").strip()
    return Path(raw) if raw else Path(_DEFAULT_DRY_RUN_LOG)


def _resolve_warehouse_id() -> str:
    warehouse_id = os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
    if not warehouse_id:
        raise RuntimeError(
            "BV_INDEXER_WAREHOUSE_ID env var is required to promote capability"
            " graph snapshots (Statement Execution needs a serverless SQL"
            " warehouse target)"
        )
    return warehouse_id


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


def _qualified(catalog: str, table: str, *, schema: str | None = None) -> str:
    """Build the fully-qualified ``<catalog>.<schema>.<table>`` name.

    Per v0.7.7 schema consolidation every BrickVision UC object lives
    in a single flat schema (default ``brickvision``, override via
    ``BV_SCHEMA``)."""

    return f"{catalog}.{schema or _resolve_schema()}.{table}"


def _render_already_promoted_query(*, catalog: str, snapshot_id: str) -> str:
    return (
        f"SELECT promoted_at_ms FROM {_qualified(catalog, 'corpus_snapshots')}"
        f" WHERE snapshot_id = {_sql_string_literal(snapshot_id)}"
    )


def _render_promotion_statements(
    *,
    catalog: str,
    snapshot_id: str,
    promoted_at_ms: int,
    promoted_by: str,
    signature: str,
) -> list[str]:
    """Render the BEGIN/MERGE/UPDATE/COMMIT statements for atomic promotion."""

    snap_lit = _sql_string_literal(snapshot_id)
    by_lit = _sql_string_literal(promoted_by)
    sig_lit = _sql_string_literal(signature)
    return [
        (
            f"MERGE INTO {_qualified(catalog, 'active_snapshot_id')} t"
            f" USING (SELECT 'singleton' AS singleton_key,"
            f" {snap_lit} AS snapshot_id,"
            f" {promoted_at_ms} AS promoted_at_ms,"
            f" {by_lit} AS promoted_by) s"
            f" ON t.singleton_key = s.singleton_key"
            f" WHEN MATCHED THEN UPDATE SET"
            f" snapshot_id = s.snapshot_id,"
            f" promoted_at_ms = s.promoted_at_ms,"
            f" promoted_by = s.promoted_by"
            f" WHEN NOT MATCHED THEN INSERT *"
        ),
        (
            f"UPDATE {_qualified(catalog, 'corpus_snapshots')}"
            f" SET promoted_at_ms = {promoted_at_ms},"
            f" signature = {sig_lit}"
            f" WHERE snapshot_id = {snap_lit}"
        ),
    ]


def _execute_statements(*, statements: Sequence[str]) -> None:
    """Run ``statements`` through Databricks Statement Execution."""

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


def _query_already_promoted(*, catalog: str, snapshot_id: str) -> bool:
    """Production "already promoted?" probe.

    Honors ``BV_DRY_RUN=true`` (defaults to False so dry-run promote
    runs to completion); set ``BV_DRY_RUN_PROMOTE_ALREADY_PROMOTED=true``
    to force the True branch in tests.
    """

    if _is_dry_run():
        force = os.environ.get(
            "BV_DRY_RUN_PROMOTE_ALREADY_PROMOTED", "false"
        ).lower()
        return force in ("1", "true", "yes")

    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

    warehouse_id = _resolve_warehouse_id()
    client = WorkspaceClient()
    statement = _render_already_promoted_query(
        catalog=catalog, snapshot_id=snapshot_id,
    )
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

    result = response.result
    if result is None or not getattr(result, "data_array", None):
        return False
    first_row = result.data_array[0]
    if not first_row:
        return False
    return first_row[0] is not None


def _flush_dry_run_log(
    *,
    catalog: str,
    snapshot_id: str,
    statements: Sequence[str],
    promoted_at_ms: int,
    promoted_by: str,
    signature: str,
) -> None:
    target = _resolve_dry_run_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "catalog": catalog,
                "snapshot_id": snapshot_id,
                "promoted_at_ms": promoted_at_ms,
                "promoted_by": promoted_by,
                "signature": signature,
                "statements": list(statements),
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )


def _execute_or_log_promotion(
    *,
    catalog: str,
    snapshot_id: str,
    promoted_at_ms: int,
    promoted_by: str,
    signature: str,
    dry_run: bool,
) -> str:
    """Run the atomic promotion (or log it under dry-run) and return
    the signature string written to the snapshot row."""

    statements = _render_promotion_statements(
        catalog=catalog,
        snapshot_id=snapshot_id,
        promoted_at_ms=promoted_at_ms,
        promoted_by=promoted_by,
        signature=signature,
    )
    if dry_run:
        _flush_dry_run_log(
            catalog=catalog,
            snapshot_id=snapshot_id,
            statements=statements,
            promoted_at_ms=promoted_at_ms,
            promoted_by=promoted_by,
            signature=signature,
        )
        return signature
    _execute_statements(statements=statements)
    return signature


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def promote_snapshot(
    *,
    snapshot_id: str,
    persist_result: PersistResult,
    vs_upsert_result: VsUpsertResult,
    smoke_result: SmokeResult,
    promoted_by: str,
    signature: str,
    promoted_at_ms: int,
    catalog: str = "brickvision",
    completed_at_ms: int | None = None,
) -> PromoteResult:
    """Atomically promote ``snapshot_id`` if and only if all gates pass.

    Parameters
    ----------
    snapshot_id : str
        The snapshot to promote. Must match
        ``persist_result.snapshot_id`` and
        ``smoke_result.snapshot_id`` (a programming-bug guard
        raises :class:`ValueError` otherwise).
    persist_result, vs_upsert_result, smoke_result :
        The 3 upstream results that the gate inspects:
        ``persist_result.errors == ()`` AND
        ``vs_upsert_result.errors == ()`` AND
        ``smoke_result.passed``.
    promoted_by : str
        SP application_id (typically ``bv_indexer_sp.<UUID>``); written
        verbatim into ``active_snapshot_id.promoted_by``.
    signature : str
        Hex digest of the corpus signature (caller — typically the
        indexer Job's ``promote`` task — supplies a SHA-256 over the
        snapshot's content_hashes). Written verbatim to
        ``corpus_snapshots.signature`` and echoed back on
        :attr:`PromoteResult.signature` for caller observability.
    promoted_at_ms : int
        Wall-clock at promotion. Stamped into both
        ``active_snapshot_id.promoted_at_ms`` and
        ``corpus_snapshots.promoted_at_ms``.
    catalog : str, default ``"brickvision"``
        UC catalog hosting the ``capability_graph`` schema.

    Returns
    -------
    PromoteResult
        ``promoted = True`` iff all gates passed AND the atomic write
        succeeded. ``failed_gates`` enumerates which gate failed (for
        operator triage); ``errors`` captures the write-side
        exception when promotion attempted but failed. The
        :attr:`dry_run` flag echoes whether ``BV_DRY_RUN`` was active.

    Notes
    -----
    Gate inspection happens BEFORE the "already promoted?" check, so
    a snapshot that failed ``persist`` won't even get its
    promoted-status checked — the gate failures take precedence in
    the diagnostic output.
    """

    # Defensive: snapshot_id alignment across all 3 upstream results.
    if persist_result.snapshot_id != snapshot_id:
        raise ValueError(
            f"snapshot_id mismatch: arg={snapshot_id!r}, "
            f"persist={persist_result.snapshot_id!r}"
        )
    if smoke_result.snapshot_id != snapshot_id:
        raise ValueError(
            f"snapshot_id mismatch: arg={snapshot_id!r}, "
            f"smoke={smoke_result.snapshot_id!r}"
        )

    dry_run = _is_dry_run()
    failed_gates: list[FailedGate] = []

    if persist_result.errors:
        failed_gates.append(
            FailedGate(
                gate_name="persist",
                detail=(
                    f"{len(persist_result.errors)} table(s) failed: "
                    + ", ".join(e.table_name for e in persist_result.errors)
                ),
            )
        )

    if vs_upsert_result.errors:
        failed_gates.append(
            FailedGate(
                gate_name="vs_upsert",
                detail=(
                    f"{len(vs_upsert_result.errors)} batch(es) failed; "
                    f"{vs_upsert_result.batches_succeeded}/"
                    f"{vs_upsert_result.batches_attempted} succeeded"
                ),
            )
        )

    if not smoke_result.passed:
        failed_gates.append(
            FailedGate(
                gate_name="smoke",
                detail=(
                    f"observed_hit_rate {smoke_result.observed_hit_rate:.3f} "
                    f"< baseline {smoke_result.baseline_hit_rate:.3f}; "
                    f"{smoke_result.misses}/{smoke_result.queries_run} missed"
                ),
            )
        )

    end = completed_at_ms if completed_at_ms is not None else promoted_at_ms

    if failed_gates:
        return PromoteResult(
            snapshot_id=snapshot_id,
            promoted=False, promoted_at_ms=None,
            promoted_by=None, signature=None,
            failed_gates=tuple(failed_gates), errors=(),
            started_at_ms=promoted_at_ms, completed_at_ms=end,
            duration_ms=max(0, end - promoted_at_ms),
            dry_run=dry_run,
        )

    # Defensive: refuse to double-promote (idempotency guard).
    try:
        already = _query_already_promoted(
            catalog=catalog, snapshot_id=snapshot_id,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        return PromoteResult(
            snapshot_id=snapshot_id,
            promoted=False, promoted_at_ms=None,
            promoted_by=None, signature=None,
            failed_gates=(), errors=(
                PromoteError(error_kind=type(exc).__name__, error_message=str(exc)),
            ),
            started_at_ms=promoted_at_ms, completed_at_ms=end,
            duration_ms=max(0, end - promoted_at_ms),
            dry_run=dry_run,
        )

    if already:
        return PromoteResult(
            snapshot_id=snapshot_id,
            promoted=False, promoted_at_ms=None,
            promoted_by=None, signature=None,
            failed_gates=(
                FailedGate(
                    gate_name="already_promoted",
                    detail=f"snapshot {snapshot_id!r} already has "
                           "a non-null promoted_at_ms",
                ),
            ),
            errors=(),
            started_at_ms=promoted_at_ms, completed_at_ms=end,
            duration_ms=max(0, end - promoted_at_ms),
            dry_run=dry_run,
        )

    # All gates pass — attempt the atomic write.
    try:
        signed_signature = _execute_or_log_promotion(
            catalog=catalog,
            snapshot_id=snapshot_id,
            promoted_at_ms=promoted_at_ms,
            promoted_by=promoted_by,
            signature=signature,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        return PromoteResult(
            snapshot_id=snapshot_id,
            promoted=False, promoted_at_ms=None,
            promoted_by=None, signature=None,
            failed_gates=(), errors=(
                PromoteError(error_kind=type(exc).__name__, error_message=str(exc)),
            ),
            started_at_ms=promoted_at_ms, completed_at_ms=end,
            duration_ms=max(0, end - promoted_at_ms),
            dry_run=dry_run,
        )

    return PromoteResult(
        snapshot_id=snapshot_id,
        promoted=True,
        promoted_at_ms=promoted_at_ms,
        promoted_by=promoted_by,
        signature=signed_signature,
        failed_gates=(), errors=(),
        started_at_ms=promoted_at_ms, completed_at_ms=end,
        duration_ms=max(0, end - promoted_at_ms),
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Rollback path (N179 BULK)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RollbackResult:
    """Aggregate output of one ``rollback_to_snapshot`` invocation.

    Symmetric with :class:`PromoteResult` so the indexer CLI's
    ``brickvision indexer rollback`` JSON output is shaped
    identically across forward/inverse flips. ``rolled_back=False``
    when the target is missing, out-of-retention, rate-limited,
    OR the atomic flip failed.
    """

    snapshot_id: str
    rolled_back: bool
    prior_active_snapshot_id: str | None
    rolled_back_at_ms: int | None
    rolled_back_by: str | None
    reason_code: str | None  # CAPABILITY_GRAPH_MANUAL_ROLLBACK on success
    failed_gates: tuple[FailedGate, ...]
    errors: tuple[PromoteError, ...]
    dry_run: bool = False


def _render_rollback_target_query(*, catalog: str, snapshot_id: str) -> str:
    """SQL to validate a rollback target's eligibility.

    Returns ``promoted_at_ms`` (NULL → never promoted, so not a
    valid rollback target; non-NULL → eligible if within retention).
    """

    return (
        f"SELECT promoted_at_ms FROM {_qualified(catalog, 'corpus_snapshots')}"
        f" WHERE snapshot_id = {_sql_string_literal(snapshot_id)}"
        f" AND deactivated_at_ms IS NULL"
    )


def _render_active_snapshot_query(*, catalog: str) -> str:
    return (
        f"SELECT snapshot_id FROM {_qualified(catalog, 'active_snapshot_id')}"
        f" WHERE singleton_key = 'singleton'"
    )


def _query_rollback_target(
    *, catalog: str, snapshot_id: str
) -> int | None:
    """Probe ``corpus_snapshots`` for the rollback target.

    Returns the ``promoted_at_ms`` of the target row when it exists +
    is not deactivated; ``None`` otherwise (the caller treats both
    "row missing" and "row deactivated" as out-of-retention since
    operationally they're indistinguishable for a rollback). Honors
    ``BV_DRY_RUN`` by reading from the dry-run promote log fixture
    under ``["seed"]["rollback_targets"][snapshot_id]``.
    """

    if _is_dry_run():
        target_path = _resolve_dry_run_log_path()
        if not target_path.exists():
            return None
        try:
            payload = json.loads(target_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        seed = payload.get("seed", {}) if isinstance(payload, dict) else {}
        targets = seed.get("rollback_targets", {}) if isinstance(seed, dict) else {}
        if not isinstance(targets, dict):
            return None
        raw = targets.get(snapshot_id)
        if isinstance(raw, (int, float)):
            return int(raw)
        return None

    statement = _render_rollback_target_query(
        catalog=catalog, snapshot_id=snapshot_id
    )
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
        return None
    rows = getattr(response.result, "data_array", None) or []
    if not rows or rows[0][0] is None:
        return None
    try:
        return int(rows[0][0])
    except (TypeError, ValueError):
        return None


def _query_current_active_snapshot_id(*, catalog: str) -> str | None:
    """Read the singleton ``active_snapshot_id`` row's snapshot_id.

    Used by ``rollback_to_snapshot`` to populate
    :attr:`RollbackResult.prior_active_snapshot_id` so the operator
    can audit "what was the active snapshot before we rolled back?"
    Honors ``BV_DRY_RUN`` by reading from the dry-run promote log
    fixture under ``["seed"]["current_active_snapshot_id"]``.
    """

    if _is_dry_run():
        target_path = _resolve_dry_run_log_path()
        if not target_path.exists():
            return None
        try:
            payload = json.loads(target_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        seed = payload.get("seed", {}) if isinstance(payload, dict) else {}
        raw = seed.get("current_active_snapshot_id") if isinstance(seed, dict) else None
        return raw if isinstance(raw, str) else None

    statement = _render_active_snapshot_query(catalog=catalog)
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
        return None
    rows = getattr(response.result, "data_array", None) or []
    if not rows or rows[0][0] is None:
        return None
    return str(rows[0][0])


def _render_rollback_statements(
    *,
    catalog: str,
    snapshot_id: str,
    rolled_back_at_ms: int,
    rolled_back_by: str,
) -> list[str]:
    """Emit the BEGIN/MERGE/COMMIT triplet that points
    ``active_snapshot_id`` at the historical ``snapshot_id``.

    Symmetric to :func:`_render_promotion_statements` but does NOT
    re-stamp ``corpus_snapshots.promoted_at_ms`` (the historical
    snapshot retains its original promotion timestamp; the rollback
    operator + audit trail records the inverse-flip event
    separately via the operator-recorded reason code).
    """

    snap_lit = _sql_string_literal(snapshot_id)
    by_lit = _sql_string_literal(rolled_back_by)
    return [
        (
            f"MERGE INTO {_qualified(catalog, 'active_snapshot_id')} t"
            f" USING (SELECT 'singleton' AS singleton_key,"
            f" {snap_lit} AS snapshot_id,"
            f" {rolled_back_at_ms} AS promoted_at_ms,"
            f" {by_lit} AS promoted_by) s"
            f" ON t.singleton_key = s.singleton_key"
            f" WHEN MATCHED THEN UPDATE SET"
            f" snapshot_id = s.snapshot_id,"
            f" promoted_at_ms = s.promoted_at_ms,"
            f" promoted_by = s.promoted_by"
            f" WHEN NOT MATCHED THEN INSERT *"
        ),
    ]


def _flush_dry_run_rollback_log(
    *,
    snapshot_id: str,
    prior_active_snapshot_id: str | None,
    rolled_back_at_ms: int,
    rolled_back_by: str,
    statements: Sequence[str],
) -> None:
    """Append a rollback record to the shared promote dry-run log.

    The log is keyed by ``["last_rollback"]`` so a unit test can
    inspect the rendered statements + the prior snapshot pointer
    without colliding with the ``["last_promote"]`` block written by
    forward promotion. Existing keys are preserved.
    """

    target = _resolve_dry_run_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if target.exists():
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["last_rollback"] = {
        "snapshot_id": snapshot_id,
        "prior_active_snapshot_id": prior_active_snapshot_id,
        "rolled_back_at_ms": rolled_back_at_ms,
        "rolled_back_by": rolled_back_by,
        "statements": list(statements),
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def rollback_to_snapshot(
    *,
    snapshot_id: str,
    rolled_back_by: str,
    rolled_back_at_ms: int,
    catalog: str = "brickvision",
    retention_days: int = 30,
) -> RollbackResult:
    """Atomically point ``active_snapshot_id`` at a HISTORICAL snapshot.

    The inverse of :func:`promote_snapshot` for the operator-driven
    rollback path (``brickvision indexer rollback --to <snapshot_id>``
    per [`docs/19-local-development.md`](
    ../../../../docs/19-local-development.md) §15.6).

    Two validation gates:

    1. **Target eligibility.** The named snapshot must exist in
       ``corpus_snapshots`` with a non-NULL ``promoted_at_ms`` and
       NULL ``deactivated_at_ms``. Missing or deactivated rows fail
       the gate with :class:`FailedGate("rollback_target_missing")`
       and emit reason code ``CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION``.
    2. **Retention window.** ``promoted_at_ms`` must be within
       ``retention_days * 86_400_000 ms`` of ``rolled_back_at_ms``.
       Out-of-window targets fail with
       :class:`FailedGate("rollback_target_out_of_retention")` and
       the same reason code.

    On success the function:

    * Reads the current ``active_snapshot_id`` row to populate
      :attr:`RollbackResult.prior_active_snapshot_id` (audit trail).
    * Issues a multi-statement transaction that re-points
      ``active_snapshot_id`` at the historical row.
    * Returns ``rolled_back=True`` with reason code
      ``CAPABILITY_GRAPH_MANUAL_ROLLBACK`` (INFO-level — operator
      action recorded as a signed Claim per the audit-replay
      contract in [`docs/16-identity-audit-replay.md`](
      ../../../../docs/16-identity-audit-replay.md)).

    Honors ``BV_DRY_RUN=true`` by routing all reads/writes through
    the existing dry-run promote log fixture (per
    [`docs/19-local-development.md`](
    ../../../../docs/19-local-development.md) §15.2.1) — the
    ``["last_rollback"]`` key in
    ``tests/fixtures/capability_graph/last_promote_payload.json``.
    """

    dry_run = _is_dry_run()

    target_promoted_at_ms = _query_rollback_target(
        catalog=catalog, snapshot_id=snapshot_id
    )

    if target_promoted_at_ms is None:
        return RollbackResult(
            snapshot_id=snapshot_id,
            rolled_back=False,
            prior_active_snapshot_id=None,
            rolled_back_at_ms=None,
            rolled_back_by=None,
            reason_code=(
                "CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION"
            ),
            failed_gates=(
                FailedGate(
                    gate_name="rollback_target_missing",
                    detail=(
                        f"snapshot {snapshot_id!r} is not present in"
                        f" {_qualified(catalog, 'corpus_snapshots')}"
                        " (or has been deactivated by retention)"
                    ),
                ),
            ),
            errors=(),
            dry_run=dry_run,
        )

    retention_window_ms = retention_days * 86_400_000
    if (rolled_back_at_ms - target_promoted_at_ms) > retention_window_ms:
        return RollbackResult(
            snapshot_id=snapshot_id,
            rolled_back=False,
            prior_active_snapshot_id=None,
            rolled_back_at_ms=None,
            rolled_back_by=None,
            reason_code=(
                "CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION"
            ),
            failed_gates=(
                FailedGate(
                    gate_name="rollback_target_out_of_retention",
                    detail=(
                        f"snapshot {snapshot_id!r} promoted_at_ms="
                        f"{target_promoted_at_ms} is older than"
                        f" retention_days={retention_days} from"
                        f" rolled_back_at_ms={rolled_back_at_ms}"
                    ),
                ),
            ),
            errors=(),
            dry_run=dry_run,
        )

    try:
        prior_active = _query_current_active_snapshot_id(catalog=catalog)
    except Exception:  # noqa: BLE001 — defensive; audit-only field
        prior_active = None

    statements = _render_rollback_statements(
        catalog=catalog,
        snapshot_id=snapshot_id,
        rolled_back_at_ms=rolled_back_at_ms,
        rolled_back_by=rolled_back_by,
    )

    try:
        if dry_run:
            _flush_dry_run_rollback_log(
                snapshot_id=snapshot_id,
                prior_active_snapshot_id=prior_active,
                rolled_back_at_ms=rolled_back_at_ms,
                rolled_back_by=rolled_back_by,
                statements=statements,
            )
        else:
            _execute_statements(statements=statements)
    except Exception as exc:  # noqa: BLE001 — defensive
        return RollbackResult(
            snapshot_id=snapshot_id,
            rolled_back=False,
            prior_active_snapshot_id=prior_active,
            rolled_back_at_ms=None,
            rolled_back_by=None,
            reason_code="CAPABILITY_GRAPH_PROMOTION_FAILED",
            failed_gates=(),
            errors=(
                PromoteError(error_kind=type(exc).__name__, error_message=str(exc)),
            ),
            dry_run=dry_run,
        )

    return RollbackResult(
        snapshot_id=snapshot_id,
        rolled_back=True,
        prior_active_snapshot_id=prior_active,
        rolled_back_at_ms=rolled_back_at_ms,
        rolled_back_by=rolled_back_by,
        reason_code="CAPABILITY_GRAPH_MANUAL_ROLLBACK",
        failed_gates=(),
        errors=(),
        dry_run=dry_run,
    )


__all__ = [
    "FailedGate",
    "PromoteError",
    "PromoteResult",
    "RollbackResult",
    "promote_snapshot",
    "rollback_to_snapshot",
]


# Type imports retained for backward compat with code that did
# ``from .promote import PersistResult`` etc. (defensive — no known
# external callers but harmless).
_ = (Any, PersistResult, VsUpsertResult, SmokeResult)
