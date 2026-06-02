"""``brickvision indexer`` — Capability Graph indexer CLI.

Per ``docs/19-local-development.md`` §15.6 (NEW v0.7.7) the CLI exposes:

- ``brickvision indexer refresh``
   Triggers an on-demand run of the ``bv_capability_indexer``
   multi-task serverless Databricks Job via
   ``databricks.sdk.WorkspaceClient.jobs.run_now``. Looks the Job up
   by its DAB-canonical name (configurable via ``BV_INDEXER_JOB_NAME``;
   default ``bv_capability_indexer``) so callers don't have to know
   the per-workspace numeric ID. Prints the resulting ``run_id`` so
   operators can follow progress in the Knowledge UI's Refresh
   history tab.

- ``brickvision indexer rollback --to <snapshot_id>``
   Rolls the active snapshot pointer back to a named historical
   ``snapshot_id``. Validates the target exists in
   ``<BV_CATALOG>.<BV_SCHEMA>.corpus_snapshots`` AND is within
   ``BV_INDEXER_SNAPSHOT_RETENTION_DAYS`` (default 30). Subject to a
   client-side rate limit of ``BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC``
   (default 3600 = 1 / hour) recorded in
   ``tests/fixtures/capability_graph/last_promote_payload.json``
   under ``["last_rollback"]["rolled_back_at_ms"]``. Emits the
   ``CAPABILITY_GRAPH_MANUAL_ROLLBACK`` reason code on success;
   ``CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION`` on miss.

- ``brickvision indexer status``
   Prints the active snapshot id, freshness (days since
   ``promoted_at_ms``), partial-source list, smoke baseline pass-rate
   (read from ``<BV_CATALOG>.<BV_SCHEMA>.corpus_health``), and
   ``criterion 13`` health summary. Honors ``--force-refresh`` to
   bypass the in-process ``_active_snapshot()`` TTL cache.

- ``brickvision indexer health``
   Like ``status`` but exits non-zero on ANY of:
     * no active snapshot (indexer never ran);
     * stale snapshot (older than
       ``BV_INDEXER_FRESHNESS_TOLERANCE_DAYS``, default 14);
     * smoke baseline pass-rate below the locked v1 floor (default
       0.95 of baseline; tunable via ``BV_INDEXER_SMOKE_FLOOR``).
   Suitable for CI / monitoring shell scripts:
   ``brickvision indexer health || alert``.

Discipline rule 15 (N189) — production-only code path
=====================================================

All four sub-commands reach the workspace through real
``databricks.sdk`` and ``brickvision_runtime.capability_graph``
imports inside function bodies. ``BV_DRY_RUN=true`` routes every
read/write through the existing fixture catalog (per
[`docs/19-local-development.md`](
../../../docs/19-local-development.md) §15.2.1):

- ``_active_snapshot()`` reads
  ``tests/fixtures/capability_graph/active_snapshot.json``;
- ``_refresh()`` reads ``tests/fixtures/capability_graph/last_refresh.json``
  and reports a synthetic ``run_id`` (so CI flows can verify the CLI
  shape without burning a real Job run);
- ``_rollback()`` reads + writes
  ``tests/fixtures/capability_graph/last_promote_payload.json``
  (the same dry-run log used by forward promotion);
- ``_status()`` and ``_health()`` use the same dry-run paths as
  ``_active_snapshot()``.

No mocks, fakes, stubs, or Protocols — see N189 close in
``docs/22-changelog.md`` §20.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from brickvision_runtime.failures import ReasonCode


_DEFAULT_JOB_NAME = "bv_capability_indexer"
_DEFAULT_FRESHNESS_TOLERANCE_DAYS = 14
_DEFAULT_SMOKE_FLOOR = 0.95
_DEFAULT_RETENTION_DAYS = 30
_DEFAULT_ROLLBACK_RATE_LIMIT_SEC = 3600  # 1 / hour


@dataclasses.dataclass(frozen=True, slots=True)
class IndexerCommandOutcome:
    """Result of one ``brickvision indexer <subcommand>`` invocation."""

    action: str  # "refresh" | "rollback" | "status" | "health"
    payload: Mapping[str, Any]
    suggested_next_action: str
    exit_code: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "action": self.action,
                "payload": dict(self.payload),
                "suggested_next_action": self.suggested_next_action,
            },
            sort_keys=True,
        )


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "").lower() in ("1", "true", "yes")


def _resolve_job_name() -> str:
    return os.environ.get("BV_INDEXER_JOB_NAME") or _DEFAULT_JOB_NAME


def _resolve_freshness_tolerance_days() -> int:
    raw = os.environ.get(
        "BV_INDEXER_FRESHNESS_TOLERANCE_DAYS", str(_DEFAULT_FRESHNESS_TOLERANCE_DAYS)
    )
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_FRESHNESS_TOLERANCE_DAYS


def _resolve_smoke_floor() -> float:
    raw = os.environ.get("BV_INDEXER_SMOKE_FLOOR", str(_DEFAULT_SMOKE_FLOOR))
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_SMOKE_FLOOR


def _resolve_retention_days() -> int:
    raw = os.environ.get(
        "BV_INDEXER_SNAPSHOT_RETENTION_DAYS", str(_DEFAULT_RETENTION_DAYS)
    )
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_RETENTION_DAYS


def _resolve_rollback_rate_limit_sec() -> int:
    raw = os.environ.get(
        "BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC", str(_DEFAULT_ROLLBACK_RATE_LIMIT_SEC)
    )
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_ROLLBACK_RATE_LIMIT_SEC


def _resolve_dry_run_refresh_path() -> Path:
    """Path to the dry-run refresh log fixture.

    Override via ``BV_DRY_RUN_REFRESH_PATH``. The fixture's
    ``run_id`` field is echoed back to the operator so a CI flow
    can pin its expected output.
    """

    override = os.environ.get("BV_DRY_RUN_REFRESH_PATH")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[3]
    return (
        repo_root
        / "tests"
        / "fixtures"
        / "capability_graph"
        / "last_refresh.json"
    )


def _resolve_dry_run_promote_path() -> Path:
    """Path to the shared promote+rollback dry-run log."""

    override = os.environ.get("BV_DRY_RUN_PROMOTE_LOG")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[3]
    return (
        repo_root
        / "tests"
        / "fixtures"
        / "capability_graph"
        / "last_promote_payload.json"
    )


def _now_ms() -> int:
    """Wall-clock helper. Honors ``BV_DRY_RUN_NOW_MS`` so test fixtures
    pin the rollback timestamp without relying on real wall-clock."""

    pinned = os.environ.get("BV_DRY_RUN_NOW_MS")
    if pinned:
        try:
            return int(pinned)
        except ValueError:
            pass
    return int(time.time() * 1000)


def _operator_id() -> str:
    """Resolve the operator identity for rollback audit attribution.

    Order: ``BV_INDEXER_OPERATOR_ID`` (explicit override) →
    ``USER`` (POSIX) → ``"unknown-operator"``. Recorded into
    ``active_snapshot_id.promoted_by`` on rollback so the audit
    trail names the human who triggered the inverse flip.
    """

    return (
        os.environ.get("BV_INDEXER_OPERATOR_ID")
        or os.environ.get("USER")
        or "unknown-operator"
    )


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def _trigger_indexer_run_via_jobs_api() -> tuple[int, int | None, str | None]:
    """Trigger ``bv_capability_indexer`` via Databricks Jobs API.

    Returns a 3-tuple: ``(exit_code, run_id, error_message)``. On
    success the exit code is 0 and ``run_id`` is the freshly-minted
    Jobs run id. On failure (no SDK installed, no auth, Job not
    found, run_now rejected) the exit code is non-zero and the
    error message is human-readable.
    """

    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"databricks-sdk import failed: {exc}"

    try:
        client = WorkspaceClient()
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"WorkspaceClient construction failed: {exc}"

    job_name = _resolve_job_name()
    matching_id: int | None = None
    try:
        for job in client.jobs.list(name=job_name):
            settings = getattr(job, "settings", None)
            settings_name = getattr(settings, "name", None) if settings else None
            if settings_name == job_name or job_name in (
                getattr(job, "name", None) or settings_name or ""
            ):
                matching_id = getattr(job, "job_id", None)
                if matching_id:
                    break
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"jobs.list failed: {exc}"

    if matching_id is None:
        return 2, None, (
            f"Job named {job_name!r} not found in this workspace."
            " Verify the bundle has been deployed via"
            " `databricks bundle deploy`."
        )

    try:
        run = client.jobs.run_now(job_id=matching_id)
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"jobs.run_now failed: {exc}"

    run_id = getattr(run, "run_id", None)
    if run_id is None and hasattr(run, "result"):
        run_id = getattr(run.result, "run_id", None)
    return 0, int(run_id) if run_id is not None else None, None


def _read_dry_run_refresh() -> tuple[int | None, str | None]:
    """Read the dry-run refresh fixture.

    Returns ``(run_id, message)``. Both may be ``None`` when the
    fixture is absent or malformed; the caller surfaces this as
    ``not_yet_implemented``.
    """

    target = _resolve_dry_run_refresh_path()
    if not target.exists():
        return None, None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, Mapping):
        return None, None
    raw_run_id = payload.get("run_id")
    raw_message = payload.get("message")
    run_id = int(raw_run_id) if isinstance(raw_run_id, (int, float)) else None
    message = raw_message if isinstance(raw_message, str) else None
    return run_id, message


def _refresh(_args: argparse.Namespace) -> int:
    if _is_dry_run():
        run_id, message = _read_dry_run_refresh()
        if run_id is None:
            outcome = IndexerCommandOutcome(
                action="refresh",
                payload={
                    "indexer_state": "dry_run_no_fixture",
                    "job_name": _resolve_job_name(),
                    "message": (
                        "BV_DRY_RUN=true but no fixture at"
                        f" {_resolve_dry_run_refresh_path()};"
                        " populate it with a `run_id` field to"
                        " exercise the success path"
                    ),
                },
                suggested_next_action=(
                    "either unset BV_DRY_RUN to hit the real Jobs"
                    " API, or write tests/fixtures/capability_graph/"
                    "last_refresh.json"
                ),
                exit_code=2,
            )
            print(outcome.to_json())
            return outcome.exit_code
        outcome = IndexerCommandOutcome(
            action="refresh",
            payload={
                "indexer_state": "triggered_dry_run",
                "job_name": _resolve_job_name(),
                "run_id": run_id,
                "message": message,
            },
            suggested_next_action=(
                "follow progress in the Knowledge UI > Refresh"
                " history tab; this was a dry-run so no real"
                " workspace activity occurred"
            ),
            exit_code=0,
        )
        print(outcome.to_json())
        return outcome.exit_code

    exit_code, run_id, error_message = _trigger_indexer_run_via_jobs_api()
    if exit_code != 0:
        outcome = IndexerCommandOutcome(
            action="refresh",
            payload={
                "indexer_state": "trigger_failed",
                "job_name": _resolve_job_name(),
                "error": error_message,
            },
            suggested_next_action=(
                "verify the Databricks profile is configured (auth"
                " env vars + workspace host) and the indexer Job is"
                " deployed via `databricks bundle deploy`; see"
                " docs/19-local-development.md §15.6 for the"
                " full triage runbook"
            ),
            exit_code=exit_code,
        )
        print(outcome.to_json())
        return exit_code

    outcome = IndexerCommandOutcome(
        action="refresh",
        payload={
            "indexer_state": "triggered",
            "job_name": _resolve_job_name(),
            "run_id": run_id,
        },
        suggested_next_action=(
            "follow progress in the Knowledge UI > Refresh history"
            " tab (search for the run_id printed above)"
        ),
        exit_code=0,
    )
    print(outcome.to_json())
    return outcome.exit_code


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def _read_last_rollback_at_ms() -> int | None:
    """Read the most recent rollback timestamp for rate-limit gating."""

    target = _resolve_dry_run_promote_path()
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    last = payload.get("last_rollback")
    if not isinstance(last, Mapping):
        return None
    raw = last.get("rolled_back_at_ms")
    return int(raw) if isinstance(raw, (int, float)) else None


def _rollback(args: argparse.Namespace) -> int:
    target_snapshot = args.to
    rate_limit_sec = _resolve_rollback_rate_limit_sec()
    now_ms = _now_ms()

    if rate_limit_sec > 0:
        last_at_ms = _read_last_rollback_at_ms()
        if last_at_ms is not None:
            elapsed_sec = (now_ms - last_at_ms) / 1000.0
            if 0 <= elapsed_sec < rate_limit_sec:
                next_allowed_ms = last_at_ms + rate_limit_sec * 1000
                outcome = IndexerCommandOutcome(
                    action="rollback",
                    payload={
                        "target_snapshot_id": target_snapshot,
                        "reason_code": (
                            ReasonCode.CAPABILITY_GRAPH_ROLLBACK_RATE_LIMITED.value
                        ),
                        "elapsed_sec_since_last_rollback": elapsed_sec,
                        "rate_limit_sec": rate_limit_sec,
                        "next_allowed_at_ms": next_allowed_ms,
                    },
                    suggested_next_action=(
                        "wait until next_allowed_at_ms or override"
                        " via BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC=0"
                    ),
                    exit_code=2,
                )
                print(outcome.to_json())
                return outcome.exit_code

    try:
        from brickvision_runtime.capability_graph import (  # noqa: PLC0415
            promote as promote_mod,
        )
        from brickvision_runtime.capability_graph import (  # noqa: PLC0415
            retrieve as retrieve_mod,
        )
    except Exception as exc:  # noqa: BLE001
        outcome = IndexerCommandOutcome(
            action="rollback",
            payload={
                "target_snapshot_id": target_snapshot,
                "indexer_state": "module_import_failed",
                "error": f"import failed: {exc}",
            },
            suggested_next_action=(
                "verify brickvision_runtime is on PYTHONPATH;"
                " confirm the install completed via"
                " `brickvision install --probe-runtime`"
            ),
            exit_code=2,
        )
        print(outcome.to_json())
        return outcome.exit_code

    catalog = os.environ.get("BV_CATALOG", "brickvision")
    retention_days = _resolve_retention_days()
    operator = _operator_id()

    result = promote_mod.rollback_to_snapshot(
        snapshot_id=target_snapshot,
        rolled_back_by=operator,
        rolled_back_at_ms=now_ms,
        catalog=catalog,
        retention_days=retention_days,
    )

    # Drop the in-process active-snapshot TTL cache so a follow-up
    # `brickvision indexer status` immediately reflects the new
    # active pointer (rule-15-compliant: only invalidates a cache,
    # never substitutes a fake response).
    try:
        retrieve_mod._invalidate_active_snapshot_cache()
    except Exception:  # noqa: BLE001
        pass

    if not result.rolled_back:
        gate_details = [
            {"gate_name": g.gate_name, "detail": g.detail}
            for g in result.failed_gates
        ]
        error_details = [
            {"error_kind": e.error_kind, "error_message": e.error_message}
            for e in result.errors
        ]
        outcome = IndexerCommandOutcome(
            action="rollback",
            payload={
                "target_snapshot_id": target_snapshot,
                "rolled_back": False,
                "reason_code": result.reason_code,
                "failed_gates": gate_details,
                "errors": error_details,
                "dry_run": result.dry_run,
            },
            suggested_next_action=(
                "verify the snapshot_id appears in"
                " `brickvision indexer status` history; check"
                " BV_INDEXER_SNAPSHOT_RETENTION_DAYS"
            ),
            exit_code=2,
        )
        print(outcome.to_json())
        return outcome.exit_code

    outcome = IndexerCommandOutcome(
        action="rollback",
        payload={
            "target_snapshot_id": target_snapshot,
            "rolled_back": True,
            "prior_active_snapshot_id": result.prior_active_snapshot_id,
            "rolled_back_at_ms": result.rolled_back_at_ms,
            "rolled_back_by": result.rolled_back_by,
            "reason_code": result.reason_code,
            "dry_run": result.dry_run,
        },
        suggested_next_action=(
            "verify via `brickvision indexer status`; the active"
            " snapshot now points to the rollback target"
        ),
        exit_code=0,
    )
    print(outcome.to_json())
    return outcome.exit_code


# ---------------------------------------------------------------------------
# status / health (read-side helpers shared by both)
# ---------------------------------------------------------------------------


def _compute_freshness_days(*, promoted_at_ms: int) -> float:
    return (_now_ms() - promoted_at_ms) / 86_400_000.0


def _read_smoke_pass_rate() -> float | None:
    """Read the most-recent smoke baseline pass-rate.

    SHELL: dry-run reads from
    ``tests/fixtures/capability_graph/last_smoke.json``'s ``pass_rate``.
    Production: queries
    ``<BV_CATALOG>.<BV_SCHEMA>.corpus_health`` for the latest row.
    Returns ``None`` when no data is available so health checks
    handle the cold-start case gracefully.
    """

    if _is_dry_run():
        repo_root = Path(__file__).resolve().parents[3]
        target = repo_root / "tests" / "fixtures" / "capability_graph" / "last_smoke.json"
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, Mapping):
            return None
        raw = payload.get("pass_rate")
        return float(raw) if isinstance(raw, (int, float)) else None

    warehouse_id = (
        os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
    )
    if not warehouse_id:
        return None
    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
        from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

        client = WorkspaceClient()
        response = client.statement_execution.execute_statement(
            statement=(
                "SELECT smoke_hit_rate"
                f" FROM {catalog}.{schema}.corpus_health"
                " WHERE smoke_hit_rate IS NOT NULL"
                " ORDER BY recorded_at_ms DESC LIMIT 1"
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

    if not rows or rows[0][0] is None:
        return None
    try:
        return float(rows[0][0])
    except (TypeError, ValueError):
        return None


def _resolve_active_snapshot(*, force_refresh: bool):
    """Wrapper that imports retrieve.py lazily (mirrors discipline-rule-15
    pattern in install/, persist/, etc.). Returns ``None`` on any
    import / call failure so the CLI degrades gracefully."""

    try:
        from brickvision_runtime.capability_graph.retrieve import (  # noqa: PLC0415
            _active_snapshot,
        )
        return _active_snapshot(force_refresh=force_refresh)
    except Exception:  # noqa: BLE001
        return None


def _status(args: argparse.Namespace) -> int:
    """Print the current indexer status as deterministic JSON."""

    force = bool(getattr(args, "force_refresh", False))
    snapshot = _resolve_active_snapshot(force_refresh=force)

    if snapshot is None:
        payload: dict[str, Any] = {
            "indexer_state": "never_run",
            "active_snapshot_id": None,
            "is_missing": True,
            "freshness_days": None,
            "smoke_pass_rate": None,
        }
        next_action = (
            "run 'brickvision indexer refresh' to trigger the first"
            " indexer run; subsequent calls will populate the"
            " <bv>.capability_graph.* tables and unblock Stage A"
            " dual-substrate retrieval"
        )
    else:
        freshness_days = _compute_freshness_days(
            promoted_at_ms=snapshot.promoted_at_ms
        )
        smoke_pass_rate = _read_smoke_pass_rate()
        payload = {
            "indexer_state": "active",
            "active_snapshot_id": snapshot.snapshot_id,
            "promoted_at_ms": snapshot.promoted_at_ms,
            "freshness_days": round(freshness_days, 2),
            "sources_complete": list(snapshot.sources_complete),
            "sources_partial": list(snapshot.sources_partial),
            "smoke_pass_rate": smoke_pass_rate,
            "is_missing": False,
        }
        next_action = (
            "snapshot is active; see /knowledge tab in the Console"
            " for the per-tab data view"
        )

    outcome = IndexerCommandOutcome(
        action="status",
        payload=payload,
        suggested_next_action=next_action,
        exit_code=0,
    )
    print(outcome.to_json())
    return outcome.exit_code


def _health(_args: argparse.Namespace) -> int:
    """Like ``status`` but exits non-zero on missing/stale/regression."""

    snapshot = _resolve_active_snapshot(force_refresh=True)

    if snapshot is None:
        outcome = IndexerCommandOutcome(
            action="health",
            payload={
                "indexer_state": "never_run",
                "is_healthy": False,
                "reason_code": ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value,
            },
            suggested_next_action=(
                "run 'brickvision indexer refresh' to trigger the"
                " first indexer run"
            ),
            exit_code=1,
        )
        print(outcome.to_json())
        return outcome.exit_code

    freshness_days = _compute_freshness_days(
        promoted_at_ms=snapshot.promoted_at_ms
    )
    tolerance_days = _resolve_freshness_tolerance_days()

    if freshness_days > tolerance_days:
        outcome = IndexerCommandOutcome(
            action="health",
            payload={
                "indexer_state": "active",
                "active_snapshot_id": snapshot.snapshot_id,
                "freshness_days": round(freshness_days, 2),
                "freshness_tolerance_days": tolerance_days,
                "is_healthy": False,
                "reason_code": ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value,
            },
            suggested_next_action=(
                "trigger a fresh refresh via"
                " 'brickvision indexer refresh', OR widen"
                " BV_INDEXER_FRESHNESS_TOLERANCE_DAYS"
            ),
            exit_code=1,
        )
        print(outcome.to_json())
        return outcome.exit_code

    smoke_pass_rate = _read_smoke_pass_rate()
    smoke_floor = _resolve_smoke_floor()
    if smoke_pass_rate is not None and smoke_pass_rate < smoke_floor:
        outcome = IndexerCommandOutcome(
            action="health",
            payload={
                "indexer_state": "active",
                "active_snapshot_id": snapshot.snapshot_id,
                "freshness_days": round(freshness_days, 2),
                "smoke_pass_rate": smoke_pass_rate,
                "smoke_floor": smoke_floor,
                "is_healthy": False,
                "reason_code": ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value,
            },
            suggested_next_action=(
                "inspect per-query diff in the Knowledge UI's smoke"
                " baseline tab; rollback via"
                " 'brickvision indexer rollback --to <prior_id>'"
                " if the regression is severe"
            ),
            exit_code=1,
        )
        print(outcome.to_json())
        return outcome.exit_code

    outcome = IndexerCommandOutcome(
        action="health",
        payload={
            "indexer_state": "active",
            "active_snapshot_id": snapshot.snapshot_id,
            "freshness_days": round(freshness_days, 2),
            "freshness_tolerance_days": tolerance_days,
            "smoke_pass_rate": smoke_pass_rate,
            "smoke_floor": smoke_floor,
            "is_healthy": True,
        },
        suggested_next_action="no action — indexer is healthy",
        exit_code=0,
    )
    print(outcome.to_json())
    return outcome.exit_code


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def add_parser(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Wire up ``brickvision indexer <subcommand>`` argparse."""

    p.set_defaults(_handler=lambda _: 0)
    sub = p.add_subparsers(dest="indexer_command", required=True)

    pr = sub.add_parser("refresh", help="trigger an on-demand indexer refresh")
    pr.set_defaults(_handler=_refresh)

    prb = sub.add_parser(
        "rollback", help="roll active snapshot back to a named one"
    )
    prb.add_argument(
        "--to",
        required=True,
        help=(
            "target snapshot_id (must be within"
            " BV_INDEXER_SNAPSHOT_RETENTION_DAYS)"
        ),
    )
    prb.set_defaults(_handler=_rollback)

    ps = sub.add_parser(
        "status", help="print active snapshot + freshness + smoke pass-rate"
    )
    ps.add_argument(
        "--force-refresh",
        action="store_true",
        help="bypass the in-process TTL cache for the active snapshot read",
    )
    ps.set_defaults(_handler=_status)

    ph = sub.add_parser(
        "health",
        help="non-zero exit on missing / stale / smoke-regression",
    )
    ph.set_defaults(_handler=_health)

    return p


__all__ = [
    "IndexerCommandOutcome",
    "add_parser",
]
