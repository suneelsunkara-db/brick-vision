"""N179 BULK — `brickvision indexer` CLI contract tests.

Per ``docs/19-local-development.md`` §15.6 the four sub-commands
(``refresh`` · ``rollback`` · ``status`` · ``health``) MUST present a
deterministic JSON shape, honor ``BV_DRY_RUN=true`` for offline test
flows, and emit the right reason codes. These tests pin those
contracts without touching a real workspace.

What this file does NOT exercise: the real Databricks Jobs API call
in ``_trigger_indexer_run_via_jobs_api`` (that's a workspace-required
integration test; ``BV_DRY_RUN=true`` short-circuits it for unit-test
runs). The wiring of the SDK call itself is verified in
``tests/unit/test_dual_substrate_n173_bulk.py``-style monkeypatch flows
in this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:  # pragma: no cover — defensive
    sys.path.insert(0, str(_SRC))


from brickvision.cli import indexer as cli_indexer
from brickvision_runtime.capability_graph import promote as promote_mod
from brickvision_runtime.capability_graph import retrieve as retrieve_mod
from brickvision_runtime.capability_graph.promote import RollbackResult
from brickvision_runtime.capability_graph.retrieve import CapabilityGraphSnapshot
from brickvision_runtime.failures import ReasonCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_outcome(capsys) -> dict:
    """Read the last printed JSON line from stdout."""

    captured = capsys.readouterr()
    return json.loads(captured.out.strip().splitlines()[-1])


def _isolated_dry_run(monkeypatch, tmp_path):
    """Set up a per-test-isolated dry-run env."""

    monkeypatch.setenv("BV_DRY_RUN", "true")
    promote_log = tmp_path / "last_promote_payload.json"
    refresh_log = tmp_path / "last_refresh.json"
    active_snapshot = tmp_path / "active_snapshot.json"
    smoke_log = tmp_path / "last_smoke.json"
    monkeypatch.setenv("BV_DRY_RUN_PROMOTE_LOG", str(promote_log))
    monkeypatch.setenv("BV_DRY_RUN_REFRESH_PATH", str(refresh_log))
    monkeypatch.setenv("BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH", str(active_snapshot))
    monkeypatch.setenv("BV_INDEXER_OPERATOR_ID", "test-operator")
    monkeypatch.setenv("BV_DRY_RUN_NOW_MS", "1713148860000")  # 2026-04-15 02:01 UTC
    # Disable rate-limit by default; tests that exercise it set their own value.
    monkeypatch.setenv("BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC", "0")
    # Drop any cached snapshot from prior tests to keep the suite hermetic.
    retrieve_mod._invalidate_active_snapshot_cache()
    return promote_log, refresh_log, active_snapshot, smoke_log


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_dry_run_with_fixture_returns_synthetic_run_id(
    monkeypatch, tmp_path, capsys
) -> None:
    """When ``BV_DRY_RUN=true`` and the fixture exists, ``refresh``
    returns the fixture's ``run_id`` and exits 0."""

    _, refresh_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    refresh_log.write_text(json.dumps({"run_id": 12345, "message": "ok"}))

    rc = cli_indexer._refresh(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert rc == 0
    assert outcome["action"] == "refresh"
    assert outcome["payload"]["run_id"] == 12345
    assert outcome["payload"]["indexer_state"] == "triggered_dry_run"


def test_refresh_dry_run_without_fixture_exits_two(
    monkeypatch, tmp_path, capsys
) -> None:
    """When the fixture is missing, ``refresh`` exits 2 with a clear
    suggested next action (no silent success)."""

    _, refresh_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    assert not refresh_log.exists()

    rc = cli_indexer._refresh(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert rc == 2
    assert outcome["payload"]["indexer_state"] == "dry_run_no_fixture"


def test_refresh_uses_configurable_job_name(monkeypatch, tmp_path, capsys) -> None:
    _, refresh_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    monkeypatch.setenv("BV_INDEXER_JOB_NAME", "bv_capability_indexer_dev")
    refresh_log.write_text(json.dumps({"run_id": 1, "message": "ok"}))

    cli_indexer._refresh(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert outcome["payload"]["job_name"] == "bv_capability_indexer_dev"


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def test_rollback_succeeds_when_target_eligible(monkeypatch, tmp_path, capsys) -> None:
    promote_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    promote_log.write_text(
        json.dumps(
            {
                "seed": {
                    "current_active_snapshot_id": "snap-2026-04-15T02-00-00Z",
                    "rollback_targets": {
                        "snap-2026-04-14T02-00-00Z": 1713062400000,
                    },
                }
            }
        )
    )

    args = argparse.Namespace(to="snap-2026-04-14T02-00-00Z")
    rc = cli_indexer._rollback(args)
    outcome = _capture_outcome(capsys)
    assert rc == 0
    assert outcome["payload"]["rolled_back"] is True
    assert outcome["payload"]["target_snapshot_id"] == "snap-2026-04-14T02-00-00Z"
    assert outcome["payload"]["prior_active_snapshot_id"] == (
        "snap-2026-04-15T02-00-00Z"
    )
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_MANUAL_ROLLBACK.value
    )

    # The dry-run log should now carry a `last_rollback` block with
    # the rendered statements.
    log_payload = json.loads(promote_log.read_text())
    assert "last_rollback" in log_payload
    assert log_payload["last_rollback"]["snapshot_id"] == (
        "snap-2026-04-14T02-00-00Z"
    )
    assert log_payload["last_rollback"]["rolled_back_by"] == "test-operator"


def test_rollback_fails_when_target_missing(monkeypatch, tmp_path, capsys) -> None:
    promote_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    promote_log.write_text(json.dumps({"seed": {"rollback_targets": {}}}))

    args = argparse.Namespace(to="snap-does-not-exist")
    rc = cli_indexer._rollback(args)
    outcome = _capture_outcome(capsys)
    assert rc == 2
    assert outcome["payload"]["rolled_back"] is False
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION.value
    )
    assert any(
        g["gate_name"] == "rollback_target_missing"
        for g in outcome["payload"]["failed_gates"]
    )


def test_rollback_fails_when_target_out_of_retention(
    monkeypatch, tmp_path, capsys
) -> None:
    promote_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    # Target promoted_at_ms = 1700000000000 (2023-11-14); now_ms is
    # 1713148860000 (2026-04-15) — that's ~152 days, well beyond the
    # default 30 day retention.
    promote_log.write_text(
        json.dumps({"seed": {"rollback_targets": {"snap-old": 1700000000000}}})
    )

    args = argparse.Namespace(to="snap-old")
    rc = cli_indexer._rollback(args)
    outcome = _capture_outcome(capsys)
    assert rc == 2
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION.value
    )
    assert any(
        g["gate_name"] == "rollback_target_out_of_retention"
        for g in outcome["payload"]["failed_gates"]
    )


def test_rollback_rate_limit_blocks_repeat_within_window(
    monkeypatch, tmp_path, capsys
) -> None:
    promote_log, *_ = _isolated_dry_run(monkeypatch, tmp_path)
    monkeypatch.setenv("BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC", "3600")
    # Pre-seed the log with a recent rollback.
    promote_log.write_text(
        json.dumps(
            {
                "last_rollback": {
                    "rolled_back_at_ms": 1713148800000,  # 1 minute ago
                    "snapshot_id": "snap-prev",
                    "rolled_back_by": "ops",
                    "statements": [],
                },
                "seed": {"rollback_targets": {"snap-target": 1713062400000}},
            }
        )
    )

    args = argparse.Namespace(to="snap-target")
    rc = cli_indexer._rollback(args)
    outcome = _capture_outcome(capsys)
    assert rc == 2
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_ROLLBACK_RATE_LIMITED.value
    )
    assert outcome["payload"]["rate_limit_sec"] == 3600


def test_rollback_invalidates_active_snapshot_cache(
    monkeypatch, tmp_path, capsys
) -> None:
    """After a successful rollback, the active-snapshot TTL cache MUST
    be cleared so a follow-up `status` reflects the new active row."""

    promote_log, _refresh_log, active_path, _smoke_log = _isolated_dry_run(
        monkeypatch, tmp_path
    )
    promote_log.write_text(
        json.dumps(
            {
                "seed": {
                    "current_active_snapshot_id": "snap-A",
                    "rollback_targets": {"snap-B": 1713062400000},
                }
            }
        )
    )
    active_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-A",
                "promoted_at_ms": 1713148800000,
                "is_active": True,
                "sources_complete": ["sdk"],
                "sources_partial": [],
            }
        )
    )

    # Warm the cache.
    snap1 = retrieve_mod._active_snapshot()
    assert snap1 is not None
    assert snap1.snapshot_id == "snap-A"

    # Re-write the fixture to a different snapshot to simulate the
    # rollback's side-effect.
    active_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-B",
                "promoted_at_ms": 1713062400000,
                "is_active": True,
                "sources_complete": ["sdk"],
                "sources_partial": [],
            }
        )
    )

    # Without invalidation the TTL cache would still return snap-A.
    args = argparse.Namespace(to="snap-B")
    rc = cli_indexer._rollback(args)
    assert rc == 0
    capsys.readouterr()  # drain

    snap2 = retrieve_mod._active_snapshot()
    assert snap2 is not None
    assert snap2.snapshot_id == "snap-B", (
        "_invalidate_active_snapshot_cache was not called; the CLI"
        " status would lie about the active snapshot for up to 60s"
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_never_run_when_no_active_snapshot(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolated_dry_run(monkeypatch, tmp_path)
    rc = cli_indexer._status(argparse.Namespace(force_refresh=False))
    outcome = _capture_outcome(capsys)
    assert rc == 0
    assert outcome["payload"]["indexer_state"] == "never_run"
    assert outcome["payload"]["is_missing"] is True


def test_status_reports_active_snapshot_with_freshness(
    monkeypatch, tmp_path, capsys
) -> None:
    _, _, active_path, _ = _isolated_dry_run(monkeypatch, tmp_path)
    active_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-2026-04-15T02-00-00Z",
                "promoted_at_ms": 1713148800000,  # 1 min before now
                "is_active": True,
                "sources_complete": ["sdk", "openapi", "docs"],
                "sources_partial": ["blog"],
            }
        )
    )
    # Pin the smoke read deterministically — exercising the real
    # `_read_smoke_pass_rate` end-to-end is left to the dedicated
    # health-path tests below.
    monkeypatch.setattr(cli_indexer, "_read_smoke_pass_rate", lambda: 1.0)

    rc = cli_indexer._status(argparse.Namespace(force_refresh=True))
    outcome = _capture_outcome(capsys)
    assert rc == 0
    assert outcome["payload"]["indexer_state"] == "active"
    assert outcome["payload"]["active_snapshot_id"] == (
        "snap-2026-04-15T02-00-00Z"
    )
    assert outcome["payload"]["sources_partial"] == ["blog"]
    assert outcome["payload"]["smoke_pass_rate"] == 1.0
    assert outcome["payload"]["freshness_days"] >= 0


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_health_exits_one_when_no_active_snapshot(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolated_dry_run(monkeypatch, tmp_path)
    rc = cli_indexer._health(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert rc == 1
    assert outcome["payload"]["is_healthy"] is False
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value
    )


def test_health_exits_one_when_snapshot_stale(
    monkeypatch, tmp_path, capsys
) -> None:
    _, _, active_path, _ = _isolated_dry_run(monkeypatch, tmp_path)
    # Snapshot promoted 30 days before now (now=1713148860000)
    promoted = 1713148860000 - 30 * 86_400_000
    active_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-stale",
                "promoted_at_ms": promoted,
                "is_active": True,
                "sources_complete": ["sdk"],
                "sources_partial": [],
            }
        )
    )
    monkeypatch.setenv("BV_INDEXER_FRESHNESS_TOLERANCE_DAYS", "14")
    monkeypatch.setattr(cli_indexer, "_read_smoke_pass_rate", lambda: 1.0)

    rc = cli_indexer._health(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert rc == 1
    assert outcome["payload"]["is_healthy"] is False
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value
    )


def test_health_exits_one_when_smoke_below_floor(
    monkeypatch, tmp_path, capsys
) -> None:
    _, _, active_path, _ = _isolated_dry_run(monkeypatch, tmp_path)
    active_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-recent",
                "promoted_at_ms": 1713148800000,
                "is_active": True,
                "sources_complete": ["sdk"],
                "sources_partial": [],
            }
        )
    )
    monkeypatch.setenv("BV_INDEXER_SMOKE_FLOOR", "0.95")
    monkeypatch.setattr(cli_indexer, "_read_smoke_pass_rate", lambda: 0.50)

    rc = cli_indexer._health(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert rc == 1
    assert outcome["payload"]["reason_code"] == (
        ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value
    )
    assert outcome["payload"]["smoke_pass_rate"] == 0.50


def test_health_exits_zero_when_healthy(monkeypatch, tmp_path, capsys) -> None:
    _, _, active_path, _ = _isolated_dry_run(monkeypatch, tmp_path)
    active_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-healthy",
                "promoted_at_ms": 1713148800000,
                "is_active": True,
                "sources_complete": ["sdk", "openapi", "docs", "labs", "blog"],
                "sources_partial": [],
            }
        )
    )
    monkeypatch.setattr(cli_indexer, "_read_smoke_pass_rate", lambda: 1.0)

    rc = cli_indexer._health(argparse.Namespace())
    outcome = _capture_outcome(capsys)
    assert rc == 0
    assert outcome["payload"]["is_healthy"] is True
    # Healthy path doesn't emit a reason code — the key MUST be absent
    # (so the JSON shape is "no reason_code field" rather than "reason_code:null").
    assert "reason_code" not in outcome["payload"]


# ---------------------------------------------------------------------------
# argparse wiring smoke
# ---------------------------------------------------------------------------


def test_add_parser_registers_four_subcommands() -> None:
    parser = argparse.ArgumentParser()
    cli_indexer.add_parser(parser)
    # Parse each sub-command's flags to confirm they're registered.
    parsed = parser.parse_args(["status"])
    assert parsed.indexer_command == "status"
    parsed = parser.parse_args(["health"])
    assert parsed.indexer_command == "health"
    parsed = parser.parse_args(["refresh"])
    assert parsed.indexer_command == "refresh"
    parsed = parser.parse_args(["rollback", "--to", "snap-x"])
    assert parsed.indexer_command == "rollback"
    assert parsed.to == "snap-x"


def test_rollback_requires_to_flag() -> None:
    parser = argparse.ArgumentParser()
    cli_indexer.add_parser(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["rollback"])


# ---------------------------------------------------------------------------
# rollback_to_snapshot helper (direct unit-level)
# ---------------------------------------------------------------------------


def test_rollback_to_snapshot_returns_failure_payload_when_target_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BV_DRY_RUN", "true")
    log = tmp_path / "promote.json"
    monkeypatch.setenv("BV_DRY_RUN_PROMOTE_LOG", str(log))
    log.write_text(json.dumps({"seed": {"rollback_targets": {}}}))

    result = promote_mod.rollback_to_snapshot(
        snapshot_id="missing",
        rolled_back_by="ops",
        rolled_back_at_ms=1713148860000,
    )
    assert isinstance(result, RollbackResult)
    assert result.rolled_back is False
    assert result.reason_code == (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION.value
    )


def test_rollback_to_snapshot_records_prior_active_id_in_audit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BV_DRY_RUN", "true")
    log = tmp_path / "promote.json"
    monkeypatch.setenv("BV_DRY_RUN_PROMOTE_LOG", str(log))
    log.write_text(
        json.dumps(
            {
                "seed": {
                    "current_active_snapshot_id": "snap-A",
                    "rollback_targets": {"snap-B": 1713062400000},
                }
            }
        )
    )
    result = promote_mod.rollback_to_snapshot(
        snapshot_id="snap-B",
        rolled_back_by="ops",
        rolled_back_at_ms=1713148860000,
    )
    assert result.rolled_back is True
    assert result.prior_active_snapshot_id == "snap-A"
    log_payload = json.loads(log.read_text())
    assert log_payload["last_rollback"]["prior_active_snapshot_id"] == "snap-A"


# ---------------------------------------------------------------------------
# _active_snapshot dry-run path
# ---------------------------------------------------------------------------


def test_active_snapshot_reads_dry_run_fixture(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BV_DRY_RUN", "true")
    fixture = tmp_path / "active.json"
    monkeypatch.setenv("BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH", str(fixture))
    fixture.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-fixture",
                "promoted_at_ms": 1713000000000,
                "is_active": True,
                "sources_complete": ["sdk", "openapi"],
                "sources_partial": ["blog"],
            }
        )
    )
    retrieve_mod._invalidate_active_snapshot_cache()
    snapshot = retrieve_mod._active_snapshot(force_refresh=True)
    assert isinstance(snapshot, CapabilityGraphSnapshot)
    assert snapshot.snapshot_id == "snap-fixture"
    assert snapshot.sources_partial == ("blog",)


def test_active_snapshot_returns_none_when_is_active_false(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BV_DRY_RUN", "true")
    fixture = tmp_path / "active.json"
    monkeypatch.setenv("BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH", str(fixture))
    fixture.write_text(
        json.dumps({"snapshot_id": "snap-x", "is_active": False})
    )
    retrieve_mod._invalidate_active_snapshot_cache()
    assert retrieve_mod._active_snapshot(force_refresh=True) is None


def test_active_snapshot_caches_within_ttl(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BV_DRY_RUN", "true")
    fixture = tmp_path / "active.json"
    monkeypatch.setenv("BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH", str(fixture))
    monkeypatch.setenv("BV_CG_ACTIVE_SNAPSHOT_TTL_SEC", "999")
    fixture.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-A",
                "promoted_at_ms": 1713000000000,
                "is_active": True,
                "sources_complete": ["sdk"],
                "sources_partial": [],
            }
        )
    )
    retrieve_mod._invalidate_active_snapshot_cache()
    s1 = retrieve_mod._active_snapshot()
    assert s1 is not None and s1.snapshot_id == "snap-A"

    # Re-write fixture; without force_refresh the cache should still serve snap-A.
    fixture.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-B",
                "promoted_at_ms": 1713000000000,
                "is_active": True,
                "sources_complete": ["sdk"],
                "sources_partial": [],
            }
        )
    )
    s2 = retrieve_mod._active_snapshot()
    assert s2 is not None and s2.snapshot_id == "snap-A", (
        "TTL cache did not return cached value within window"
    )

    s3 = retrieve_mod._active_snapshot(force_refresh=True)
    assert s3 is not None and s3.snapshot_id == "snap-B"
