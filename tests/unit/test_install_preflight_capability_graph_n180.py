"""N180 BULK — install pre-flight wiring for the v0.7.7 Capability-Graph gates.

These tests pin:

1. **Probe builders read fixtures correctly** under ``BV_DRY_RUN=true``.
2. **Runner wrappers integrate probes + check functions** end-to-end and
   surface the right ``ReasonCode`` on each gate's failure mode.
3. **Install CLI integration** — ``default_preflights()`` includes the 4
   capability-graph gates by default; ``BV_CAPABILITY_GRAPH_ENABLED=false``
   suppresses them.
4. **Probe-unavailable failure mode** is reachable + emits the
   right reason code without crashing the install runner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:  # pragma: no cover — defensive
    sys.path.insert(0, str(_SRC))


from brickvision.cli import install as install_cli
from brickvision.install.preflight.capability_graph import (
    BudgetNamespaceProbe,
    BudgetNamespaceSpec,
    IndexerSPProbe,
    IndexerSPSpec,
    UCSchemaProbe,
    UCSchemaSpec,
    VSGrantProbe,
    VSGrantSpec,
)
from brickvision.install.preflight.capability_graph_probes import (
    build_budget_namespace_probe,
    build_indexer_sp_probe,
    build_uc_schema_probe,
    build_vs_grants_probe,
)
from brickvision_runtime.failures import ReasonCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolate_dry_run(monkeypatch, tmp_path):
    """Per-test-isolated dry-run env with override paths to tmp fixtures."""

    monkeypatch.setenv("BV_DRY_RUN", "true")
    paths = {
        "indexer_sp": tmp_path / "indexer_sp.json",
        "budget_namespaces": tmp_path / "budget_namespaces.json",
        "uc_schema": tmp_path / "uc_schema.json",
        "vs_grants": tmp_path / "vs_grants.json",
    }
    for name, path in paths.items():
        env_key = f"BV_DRY_RUN_PREFLIGHT_{name.upper()}_PATH"
        monkeypatch.setenv(env_key, str(path))
    return paths


# ---------------------------------------------------------------------------
# Probe builder happy paths
# ---------------------------------------------------------------------------


def test_indexer_sp_probe_loads_fixture(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["indexer_sp"].write_text(
        json.dumps(
            {
                "indexer_sp_application_id": "id-1",
                "app_sp_application_id": "id-2",
                "enabled": {"bv_indexer_sp": True, "bv_app_sp": True},
            }
        )
    )
    probe = build_indexer_sp_probe(spec=IndexerSPSpec())
    assert isinstance(probe, IndexerSPProbe)
    assert probe.indexer_sp_application_id == "id-1"
    assert probe.app_sp_application_id == "id-2"
    assert probe.enabled == {"bv_indexer_sp": True, "bv_app_sp": True}


def test_budget_namespace_probe_loads_fixture(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["budget_namespaces"].write_text(
        json.dumps(
            {
                "namespaces": {"app": "ledger_app", "indexer": "ledger_idx"},
                "env_resolution": {"bv_app_sp": "app", "bv_indexer_sp": "indexer"},
            }
        )
    )
    probe = build_budget_namespace_probe(spec=BudgetNamespaceSpec())
    assert isinstance(probe, BudgetNamespaceProbe)
    assert probe.namespaces == {"app": "ledger_app", "indexer": "ledger_idx"}
    assert probe.env_resolution["bv_indexer_sp"] == "indexer"


def test_uc_schema_probe_loads_fixture(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["uc_schema"].write_text(
        json.dumps(
            {
                "exists": True,
                "owner": "bv_indexer_sp",
                "grants": {
                    "bv_indexer_sp": ["ALL_PRIVILEGES"],
                    "bv_app_sp": ["SELECT"],
                },
            }
        )
    )
    probe = build_uc_schema_probe(
        spec=UCSchemaSpec(schema_full_name="brickvision.brickvision")
    )
    assert isinstance(probe, UCSchemaProbe)
    assert probe.exists is True
    assert probe.owner == "bv_indexer_sp"
    assert probe.grants["bv_app_sp"] == ("SELECT",)


def test_vs_grants_probe_loads_fixture(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["vs_grants"].write_text(
        json.dumps(
            {
                "endpoint_exists": True,
                "index_grants": {
                    "entity_index": {
                        "bv_indexer_sp": ["WRITE"],
                        "bv_app_sp": ["READ"],
                    }
                },
            }
        )
    )
    probe = build_vs_grants_probe(spec=VSGrantSpec())
    assert isinstance(probe, VSGrantProbe)
    assert probe.endpoint_exists is True
    assert probe.index_grants["entity_index"]["bv_indexer_sp"] == ("WRITE",)


# ---------------------------------------------------------------------------
# Probe builder graceful-degrade paths
# ---------------------------------------------------------------------------


def test_probe_returns_none_when_fixture_missing(monkeypatch, tmp_path) -> None:
    """Each builder MUST return ``None`` when the fixture is absent —
    triggering the "probe unavailable" failure path in the runner."""

    _isolate_dry_run(monkeypatch, tmp_path)
    assert build_indexer_sp_probe(spec=IndexerSPSpec()) is None
    assert build_budget_namespace_probe(spec=BudgetNamespaceSpec()) is None
    assert build_uc_schema_probe(
        spec=UCSchemaSpec(schema_full_name="brickvision.brickvision")
    ) is None
    assert build_vs_grants_probe(spec=VSGrantSpec()) is None


def test_probe_returns_none_on_malformed_json(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["indexer_sp"].write_text("{ this is not valid json")
    assert build_indexer_sp_probe(spec=IndexerSPSpec()) is None


# ---------------------------------------------------------------------------
# Runner wrapper end-to-end
# ---------------------------------------------------------------------------


def test_runner_succeeds_for_clean_workspace(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["indexer_sp"].write_text(
        json.dumps(
            {
                "indexer_sp_application_id": "id-1",
                "app_sp_application_id": "id-2",
                "enabled": {"bv_indexer_sp": True, "bv_app_sp": True},
            }
        )
    )
    paths["budget_namespaces"].write_text(
        json.dumps(
            {
                "namespaces": {"app": "ledger_app", "indexer": "ledger_idx"},
                "env_resolution": {"bv_app_sp": "app", "bv_indexer_sp": "indexer"},
            }
        )
    )
    paths["uc_schema"].write_text(
        json.dumps(
            {
                "exists": True,
                "owner": "bv_indexer_sp",
                "grants": {
                    "bv_indexer_sp": ["ALL_PRIVILEGES"],
                    "bv_app_sp": ["SELECT"],
                },
            }
        )
    )
    paths["vs_grants"].write_text(
        json.dumps(
            {
                "endpoint_exists": True,
                "index_grants": {
                    "entity_index": {
                        "bv_indexer_sp": ["WRITE"],
                        "bv_app_sp": ["READ"],
                    },
                },
            }
        )
    )

    assert install_cli._check_indexer_sp_provisioned() is None
    assert install_cli._check_indexer_budget_namespace_isolated() is None
    assert install_cli._check_uc_schema_capability_graph_ownership() is None
    assert install_cli._check_vector_search_endpoint_grants() is None


def test_runner_fails_when_indexer_sp_missing(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["indexer_sp"].write_text(
        json.dumps(
            {
                "indexer_sp_application_id": None,
                "app_sp_application_id": "id-2",
                "enabled": {"bv_app_sp": True},
            }
        )
    )
    failure = install_cli._check_indexer_sp_provisioned()
    assert failure is not None
    assert failure.reason_code == ReasonCode.INDEXER_SP_NOT_PROVISIONED


def test_runner_fails_when_budget_namespaces_overlap(monkeypatch, tmp_path) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["budget_namespaces"].write_text(
        json.dumps(
            {
                "namespaces": {"app": "shared_ledger", "indexer": "shared_ledger"},
                "env_resolution": {},
            }
        )
    )
    failure = install_cli._check_indexer_budget_namespace_isolated()
    assert failure is not None
    assert failure.reason_code == ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED


def test_runner_fails_when_app_sp_has_modify_on_capability_graph(
    monkeypatch, tmp_path
) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["uc_schema"].write_text(
        json.dumps(
            {
                "exists": True,
                "owner": "bv_indexer_sp",
                "grants": {
                    "bv_indexer_sp": ["ALL_PRIVILEGES"],
                    "bv_app_sp": ["SELECT", "MODIFY"],
                },
            }
        )
    )
    failure = install_cli._check_uc_schema_capability_graph_ownership()
    assert failure is not None
    assert failure.reason_code == ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID


def test_runner_fails_when_app_sp_has_write_on_vs_index(
    monkeypatch, tmp_path
) -> None:
    paths = _isolate_dry_run(monkeypatch, tmp_path)
    paths["vs_grants"].write_text(
        json.dumps(
            {
                "endpoint_exists": True,
                "index_grants": {
                    "entity_index": {
                        "bv_indexer_sp": ["WRITE"],
                        "bv_app_sp": ["WRITE"],  # <-- forbidden
                    },
                },
            }
        )
    )
    failure = install_cli._check_vector_search_endpoint_grants()
    assert failure is not None
    assert failure.reason_code == ReasonCode.VS_ENDPOINT_GRANTS_MIXED


# ---------------------------------------------------------------------------
# Probe-unavailable failure mode
# ---------------------------------------------------------------------------


def test_probe_unavailable_emits_named_reason_code(monkeypatch, tmp_path) -> None:
    """When the probe builder returns ``None`` (missing fixture), the
    runner MUST emit the gate's reason code so the partner sees the
    typed Question rather than a generic crash."""

    _isolate_dry_run(monkeypatch, tmp_path)
    f1 = install_cli._check_indexer_sp_provisioned()
    assert f1 is not None
    assert f1.reason_code == ReasonCode.INDEXER_SP_NOT_PROVISIONED
    assert "probe_unavailable" in f1.detail

    f2 = install_cli._check_indexer_budget_namespace_isolated()
    assert f2 is not None
    assert f2.reason_code == ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED

    f3 = install_cli._check_uc_schema_capability_graph_ownership()
    assert f3 is not None
    assert f3.reason_code == ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID

    f4 = install_cli._check_vector_search_endpoint_grants()
    assert f4 is not None
    assert f4.reason_code == ReasonCode.VS_ENDPOINT_GRANTS_MIXED


# ---------------------------------------------------------------------------
# Install CLI integration
# ---------------------------------------------------------------------------


def test_default_preflights_includes_four_capability_graph_gates(
    monkeypatch,
) -> None:
    monkeypatch.delenv("BV_CAPABILITY_GRAPH_ENABLED", raising=False)
    pf = install_cli.default_preflights()
    check_ids = [p.check_id for p in pf]
    assert "indexer_sp_provisioned" in check_ids
    assert "indexer_budget_namespace_isolated" in check_ids
    assert "uc_schema_capability_graph_ownership" in check_ids
    assert "vector_search_endpoint_grants" in check_ids
    # The 4 base gates are still present.
    assert "env_vars" in check_ids
    assert "runtime_isolation" in check_ids


def test_default_preflights_skips_capability_graph_when_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BV_CAPABILITY_GRAPH_ENABLED", "false")
    pf = install_cli.default_preflights()
    check_ids = [p.check_id for p in pf]
    for cg_id in (
        "indexer_sp_provisioned",
        "indexer_budget_namespace_isolated",
        "uc_schema_capability_graph_ownership",
        "vector_search_endpoint_grants",
    ):
        assert cg_id not in check_ids


def test_run_install_aggregates_capability_graph_failures(
    monkeypatch, tmp_path, capsys
) -> None:
    """End-to-end: under ``BV_DRY_RUN=true`` with **no** fixtures, the
    install runner produces 4 typed questions (one per capability-graph
    gate) and aborts with overall_passed=False — all without crashing."""

    paths = _isolate_dry_run(monkeypatch, tmp_path)
    # No fixtures written — every probe builder returns None.
    monkeypatch.setenv("BV_VISUAL_BUILDER_ENABLED", "false")  # skip dist check
    # The env-var pre-flight needs at least one required env var present
    # to pass; pin a no-op satisfaction.
    monkeypatch.setenv("BV_CATALOG", "brickvision")
    monkeypatch.setenv("BV_OPENAI_API_KEY", "test")
    monkeypatch.setenv("BV_OPENAI_AGENT_MODEL", "gpt-test")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test")

    result = install_cli.run_install()
    assert result.overall_passed is False
    failed_check_ids = {check_id for check_id, _ in result.failures}
    # At least the 4 capability-graph gates must show up.
    assert "indexer_sp_provisioned" in failed_check_ids
    assert "indexer_budget_namespace_isolated" in failed_check_ids
    assert "uc_schema_capability_graph_ownership" in failed_check_ids
    assert "vector_search_endpoint_grants" in failed_check_ids
