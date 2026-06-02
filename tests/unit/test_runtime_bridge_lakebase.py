"""v0.7.7 — runtime_bridge Lakebase read contract.

Pins the shape of every ``/api/knowledge/*`` payload that the
FastAPI sidecar serves once the indexer's T14 publish task has
populated the 10 UI-readable Synced Tables in Lakebase Postgres.

Two execution modes are pinned here:

1. **Lakebase NOT configured** — ``BV_LAKEBASE_PROJECT_ID`` /
   ``BV_LAKEBASE_DATABASE`` are absent; every endpoint returns the
   SPA-safe empty/banner payload without trying to import psycopg or
   the Databricks SDK. This is the local-dev-before-publish-runs
   state and the production-bootstrap state.

2. **Lakebase IS configured** — the query helpers (``_query_one`` /
   ``_query_all``) are monkeypatched to return canned tuples shaped
   exactly like Synced-Tables Postgres rows; the bridge's row-mapping
   code path is exercised end-to-end without ever opening a real
   connection. (Live integration tests run separately against an
   ephemeral Lakebase project.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CONSOLE_API_SRC = _REPO_ROOT / "apps" / "console-api" / "src"
if str(_CONSOLE_API_SRC) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_API_SRC))


from console_api import databricks_sql  # noqa: E402
from console_api import runtime_bridge as bridge  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to "Lakebase not configured" + reset module token cache."""

    for key in (
        "BV_LAKEBASE_PROJECT_ID",
        "BV_LAKEBASE_DATABASE",
        "BV_LAKEBASE_BRANCH",
        "BV_DRY_RUN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BV_SCHEMA", "brickvision")

    bridge._token_cache["token"] = None
    bridge._token_cache["principal"] = None
    bridge._token_cache["expires_at"] = 0.0
    bridge._host_cache["host"] = ""


@pytest.fixture
def configured_lakebase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env knobs so ``_lakebase_configured()`` returns True.

    Tests that consume this also monkeypatch ``_query_all`` /
    ``_query_one`` so the bridge never opens a real connection.
    The Postgres hostname is auto-resolved via the SDK at connection
    time, so the fixture doesn't need to plant a host env var.
    """

    monkeypatch.setenv("BV_LAKEBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("BV_LAKEBASE_DATABASE", "databricks_postgres")
    monkeypatch.setenv("BV_LAKEBASE_BRANCH", "production")


# ---------------------------------------------------------------------------
# _lakebase_configured / _sanitize_ident
# ---------------------------------------------------------------------------


def test_lakebase_configured_false_when_any_required_env_missing() -> None:
    assert bridge._lakebase_configured() is False


def test_lakebase_configured_true_when_all_required_envs_set(
    configured_lakebase: None,  # noqa: ARG001 — fixture sets env
) -> None:
    assert bridge._lakebase_configured() is True


@pytest.mark.parametrize(
    "missing_var",
    ["BV_LAKEBASE_PROJECT_ID", "BV_LAKEBASE_DATABASE"],
)
def test_lakebase_configured_false_when_one_of_two_required_unset(
    monkeypatch: pytest.MonkeyPatch, missing_var: str,
) -> None:
    """``_lakebase_configured()`` requires PROJECT_ID and DATABASE.

    The Postgres host/port are SDK-resolved (host) or fixed (port =
    5432), so the operator only configures these two env knobs to
    enable Lakebase reads. Pin that contract here so a future env-
    var addition doesn't silently expand the required set.
    """

    monkeypatch.setenv("BV_LAKEBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("BV_LAKEBASE_DATABASE", "databricks_postgres")
    monkeypatch.delenv(missing_var, raising=False)
    assert bridge._lakebase_configured() is False


def test_sanitize_ident_accepts_alnum_and_underscore() -> None:
    assert bridge._sanitize_ident("brickvision") == "brickvision"
    assert bridge._sanitize_ident("bv_schema_v0_7_7") == "bv_schema_v0_7_7"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "drop table x",
        "schema; --",
        "schema'a",
        "schema-name",
        "schema.dot",
    ],
)
def test_sanitize_ident_rejects_unsafe_inputs(bad: str) -> None:
    with pytest.raises(ValueError):
        bridge._sanitize_ident(bad)


# ---------------------------------------------------------------------------
# Postgres host auto-resolution
# ---------------------------------------------------------------------------


class _FakeBranch:
    """Minimal stand-in for the SDK's ``DatabaseBranch`` response.

    We intentionally avoid mocking the full SDK; the bridge's
    ``_extract_pg_host`` and ``_strip_to_host`` helpers are pure and
    work on any duck-typed object that exposes the documented
    attribute paths.
    """

    def __init__(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


def test_extract_pg_host_prefers_direct_pg_endpoint() -> None:
    branch = _FakeBranch(pg_endpoint="ep-direct.example.com")
    assert bridge._extract_pg_host(branch) == "ep-direct.example.com"


def test_extract_pg_host_falls_back_to_connection_host() -> None:
    branch = _FakeBranch(connection=_FakeBranch(host="ep-conn.example.com"))
    assert bridge._extract_pg_host(branch) == "ep-conn.example.com"


def test_extract_pg_host_parses_url_when_only_url_present() -> None:
    branch = _FakeBranch(
        endpoint=_FakeBranch(
            url="postgresql://user:pass@ep-url.example.com:5432/db",
        ),
    )
    assert bridge._extract_pg_host(branch) == "ep-url.example.com"


def test_extract_pg_host_returns_empty_when_nothing_matches() -> None:
    branch = _FakeBranch(unrelated="x")
    assert bridge._extract_pg_host(branch) == ""


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@host.example.com:5432/db", "host.example.com"),
        ("postgres://host.example.com/db", "host.example.com"),
        ("host.example.com:5432", "host.example.com"),
        ("host.example.com", "host.example.com"),
    ],
)
def test_strip_to_host_handles_common_dsn_shapes(url: str, expected: str) -> None:
    assert bridge._strip_to_host(url) == expected


# ---------------------------------------------------------------------------
# Empty-state path (Lakebase NOT configured)
# ---------------------------------------------------------------------------


def test_corpus_returns_banner_when_lakebase_not_configured() -> None:
    out = bridge.get_capability_graph_corpus(user_id="u")
    assert out["sources"] == []
    assert out["indexer_state"] == "never_run"
    assert "indexer has not yet" in out["message"]


def test_top_orders_returns_empty_list_when_lakebase_not_configured() -> None:
    assert bridge.list_top_orders(user_id="u") == []


def test_meta_skills_returns_empty_list_when_lakebase_not_configured() -> None:
    assert bridge.list_meta_skills(user_id="u") == []
    assert bridge.list_meta_skills(user_id="u", top_order="to:foo") == []


def test_extensions_returns_empty_list_when_lakebase_not_configured() -> None:
    assert bridge.list_extensions(user_id="u") == []
    assert bridge.list_extensions(
        user_id="u", meta_skill="meta:delta-lake", has_exemplar=True,
    ) == []


def test_refresh_history_returns_empty_list_when_lakebase_not_configured() -> None:
    assert bridge.get_capability_graph_refresh_history(user_id="u") == []


def test_health_returns_missing_banner_when_lakebase_not_configured() -> None:
    out = bridge.get_capability_graph_health(user_id="u")
    assert out["is_missing"] is True
    assert out["active_snapshot_id"] is None
    assert out["indexer_state"] == "never_run"


def test_query_helpers_return_empty_when_lakebase_not_configured() -> None:
    """The fall-through in ``_query_all`` / ``_query_one`` must never
    raise — the SPA depends on the empty result, not an exception."""

    assert bridge._query_all("SELECT 1") == []
    assert bridge._query_one("SELECT 1") is None


# ---------------------------------------------------------------------------
# Configured path — query helpers monkeypatched (no real Postgres)
# ---------------------------------------------------------------------------


def _patch_active(
    monkeypatch: pytest.MonkeyPatch, snapshot_id: str = "snap_test_01",
) -> None:
    """Pin the active-snapshot pointer for an "indexer has run" test."""

    monkeypatch.setattr(
        bridge, "_active_snapshot", lambda: (snapshot_id, 1_730_000_000_000),
    )


def test_top_orders_maps_synced_rows_to_ui_shape(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    monkeypatch.setattr(
        bridge,
        "_query_all",
        lambda *a, **k: [
            ("to:data-architecture-design", "Data Architecture Design", 12, 110, 5),
            ("to:data-engineering-design", "Data Engineering Design", 10, 95, 4),
        ],
    )
    out = bridge.list_top_orders(user_id="u")
    assert len(out) == 2
    assert out[0] == {
        "top_order_id": "to:data-architecture-design",
        "label": "Data Architecture Design",
        "meta_skill_count": 12,
        "extension_count": 110,
        "hand_authored_exemplar_count": 5,
    }


def test_meta_skills_filters_by_top_order_when_provided(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        captured["sql"] = sql
        captured["params"] = params
        return [("meta:delta-lake", "Delta Lake", "to:data-engineering-design", 42, 3)]

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    rows = bridge.list_meta_skills(user_id="u", top_order="to:data-engineering-design")
    assert rows == [
        {
            "meta_skill_id": "meta:delta-lake",
            "label": "Delta Lake",
            "parent_top_order": "to:data-engineering-design",
            "extension_count": 42,
            "hand_authored_exemplar_count": 3,
        }
    ]
    assert "AND m.top_order_id = %s" in captured["sql"]
    assert captured["params"][-1] == "to:data-engineering-design"


def test_meta_skills_omits_filter_when_top_order_none(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    bridge.list_meta_skills(user_id="u")
    assert "AND m.top_order_id" not in captured["sql"]
    assert captured["params"] == ("snap_test_01",)


def test_extensions_applies_meta_skill_and_exemplar_filters(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        captured["sql"] = sql
        captured["params"] = params
        return [
            (
                "meta:delta-lake/ext:create-table",
                "Create Delta Table",
                "meta:delta-lake",
                "write",
                "invariant",
                "skill:uc.create-table",
            ),
        ]

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    rows = bridge.list_extensions(
        user_id="u",
        meta_skill="meta:delta-lake",
        has_exemplar=True,
        limit=50,
        offset=0,
    )
    assert rows[0]["has_exemplar"] is True
    assert rows[0]["exemplar_skill_id"] == "skill:uc.create-table"
    assert "AND meta_skill_id = %s" in captured["sql"]
    assert "AND exemplar_skill_id IS NOT NULL" in captured["sql"]
    assert captured["params"][1] == "meta:delta-lake"
    assert captured["params"][-2:] == (50, 0)


def test_extensions_has_exemplar_false_filter(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    captured: dict[str, str] = {}

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        captured["sql"] = sql
        return []

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    bridge.list_extensions(user_id="u", has_exemplar=False)
    assert "AND exemplar_skill_id IS NULL" in captured["sql"]


def test_extensions_clamps_pagination_args(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        captured["params"] = params
        return []

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    bridge.list_extensions(user_id="u", limit=99999, offset=-50)
    assert captured["params"][-2] == 1000  # clamped down
    assert captured["params"][-1] == 0     # clamped up


def test_corpus_joins_health_with_authority_and_static_url_root(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    monkeypatch.setattr(
        bridge,
        "_query_all",
        lambda *a, **k: [
            ("sdk", 1_700_000_000_000, "ok", 250, 1.0),
            ("docs", 1_700_000_000_000, "partial", 500, 0.7),
        ],
    )
    out = bridge.get_capability_graph_corpus(user_id="u")
    assert out["indexer_state"] == "active"
    assert out["sources"][0] == {
        "source_id": "sdk",
        "url_root": "https://github.com/databricks/databricks-sdk-py",
        "source_authority": 1.0,
        "last_refresh_ts": 1_700_000_000_000,
        "state": "ok",
        "extension_count": 250,
    }
    assert out["sources"][1]["url_root"] == "https://docs.databricks.com/"


def test_refresh_history_maps_refresh_plan_rows(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bridge,
        "_query_all",
        lambda *a, **k: [
            (
                "rp_2026_05_01",
                1_700_000_000_000,
                3_600_000,
                "snap_2026_05_01",
                "success",
                ["blog"],
            ),
        ],
    )
    out = bridge.get_capability_graph_refresh_history(user_id="u", limit=10)
    assert out == [
        {
            "run_id": "rp_2026_05_01",
            "started_at_ms": 1_700_000_000_000,
            "ended_at_ms": 1_700_000_000_000 + 3_600_000,
            "snapshot_id": "snap_2026_05_01",
            "state": "success",
            "rejection_reason_code": None,
            "partial_sources": ["blog"],
            "total_input_tokens": 0,
        },
    ]


def test_health_computes_freshness_against_promoted_timestamp(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    promoted_ms = 1_700_000_000_000
    now_ms = promoted_ms + (5 * 24 * 3600 * 1000)  # 5 days later

    monkeypatch.setattr(
        bridge, "_active_snapshot", lambda: ("snap_test_health", promoted_ms),
    )
    monkeypatch.setattr(
        bridge, "_query_one", lambda *a, **k: ([], promoted_ms, 60_000),
    )
    # Replace the bridge's reference to the time module with a shim so
    # we don't mutate the global `time` module under other tests.
    monkeypatch.setattr(bridge, "time", types.SimpleNamespace(time=lambda: now_ms / 1000))

    monkeypatch.setenv("BV_INDEXER_FRESHNESS_TOLERANCE_DAYS", "2")
    out = bridge.get_capability_graph_health(user_id="u")
    assert out["is_missing"] is False
    assert out["active_snapshot_id"] == "snap_test_health"
    assert out["freshness_days"] == 5
    assert out["freshness_tolerance_days"] == 2
    assert out["is_stale"] is True
    assert out["partial_sources"] == []


def test_provenance_active_snapshot_extension_not_found_returns_safe_payload(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an extension id is requested that doesn't exist in the
    active snapshot, the bridge must still echo every required key
    (the SPA's pane reads them unconditionally)."""

    _patch_active(monkeypatch)
    monkeypatch.setattr(bridge, "_query_one", lambda *a, **k: None)
    monkeypatch.setattr(bridge, "_query_all", lambda *a, **k: [])

    out = bridge.get_extension_provenance(
        user_id="u", extension_id="meta:delta-lake/ext:does-not-exist",
    )
    assert out["extension_id"] == "meta:delta-lake/ext:does-not-exist"
    assert out["active_snapshot_id"] == "snap_test_01"
    assert out["indexer_state"] == "active"
    assert out["contributing_chunks"] == []
    assert out["two_hop_neighbors"] == []
    assert out["label"] is None


def test_provenance_active_snapshot_full_payload_shape(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)

    def fake_query_one(sql: str, params: tuple[Any, ...] = ()) -> Any:
        if "FROM brickvision.extensions_synced" in sql:
            return ("Create Delta Table", "meta:delta-lake", "write", "invariant", "high")
        return None

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if "source_provenance_synced" in sql:
            return [
                ("sdk", "github.com/.../delta.py:42", "abc123", 1_700_000_000_000, 1.0),
                ("docs", "https://docs.databricks.com/...", None, 1_700_000_000_000, 0.7),
            ]
        if "entity_edges_synced" in sql:
            return [
                ("meta:delta-lake/ext:upsert", "Upsert", "sibling", 1),
                ("meta:delta-lake/ext:vacuum", "Vacuum", "sibling", 2),
            ]
        return []

    monkeypatch.setattr(bridge, "_query_one", fake_query_one)
    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    out = bridge.get_extension_provenance(
        user_id="u", extension_id="meta:delta-lake/ext:create-table",
    )
    assert out["label"] == "Create Delta Table"
    assert out["parent_meta_skill"] == "meta:delta-lake"
    assert out["effect_class"] == "write"
    assert out["cloud_variance"] == "invariant"
    assert out["authority_scorer"] == "high"
    assert len(out["contributing_chunks"]) == 2
    assert out["contributing_chunks"][0]["source_id"] == "sdk"
    assert out["contributing_chunks"][0]["authority_score"] == 1.0
    assert out["contributing_chunks"][0]["commit_sha"] == "abc123"
    assert len(out["two_hop_neighbors"]) == 2
    assert out["two_hop_neighbors"][0]["hop"] == 1
    assert out["two_hop_neighbors"][1]["hop"] == 2


# ---------------------------------------------------------------------------
# Active-snapshot resolver — uses Lakebase singleton row
# ---------------------------------------------------------------------------


def test_active_snapshot_returns_none_when_lakebase_not_configured() -> None:
    assert bridge._active_snapshot() is None


def test_active_snapshot_reads_singleton_row_when_configured(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_query_one(sql: str, params: tuple[Any, ...] = ()) -> Any:
        captured["sql"] = sql
        captured["params"] = params
        return ("snap_singleton_01", 1_700_000_000_000)

    monkeypatch.setattr(bridge, "_query_one", fake_query_one)
    out = bridge._active_snapshot()
    assert out == ("snap_singleton_01", 1_700_000_000_000)
    assert "active_snapshot_id_synced" in captured["sql"]
    assert captured["params"] == ("singleton",)


# ---------------------------------------------------------------------------
# Workspace build suggestions — schema-level compiler
# ---------------------------------------------------------------------------


def _anchor_rows() -> list[tuple[str, str, str]]:
    return [
        (extension_id, skill_id, f"{skill_id} title")
        for skill_id, extension_id in bridge._REQUIRED_SUGGESTION_ANCHORS.items()
    ]


def _profile_claim_rows(
    table_ref: str,
    *,
    row_count: int,
    columns: tuple[str, ...] = ("id", "amount"),
    observed_at_ms: int = 1_700_000_000_000,
) -> list[tuple[Any, ...]]:
    subject = f"table:{table_ref}"
    rows: list[tuple[Any, ...]] = [
        (
            subject,
            "ROW_COUNT",
            None,
            json.dumps({"row_count": row_count}),
            "{}",
            observed_at_ms,
        ),
        (
            subject,
            "GRAIN_CHECK",
            None,
            json.dumps({"candidate_key_columns": [columns[0]]}),
            "{}",
            observed_at_ms,
        ),
    ]
    for column in columns:
        rows.extend(
            [
                (
                    subject,
                    "HAS_COLUMN",
                    f"{table_ref}.{column}",
                    json.dumps({"column_name": column}),
                    "{}",
                    observed_at_ms,
                ),
                (
                    subject,
                    "NULL_COUNT",
                    f"{table_ref}.{column}",
                    json.dumps({"column": column, "null_count": 0}),
                    "{}",
                    observed_at_ms,
                ),
                (
                    subject,
                    "DISTINCT_COUNT",
                    f"{table_ref}.{column}",
                    json.dumps({"column": column, "distinct_count": row_count}),
                    "{}",
                    observed_at_ms,
                ),
            ]
        )
    return rows


def test_workspace_build_suggestions_are_grouped_by_schema(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    claim_rows = (
        _profile_claim_rows("partner_demo_catalog.banking.transactions", row_count=100)
        + _profile_claim_rows("partner_demo_catalog.banking.customers", row_count=20)
        + _profile_claim_rows(
            "partner_demo_catalog.banking.__materialization_mat_hidden_1",
            row_count=999,
        )
        + _profile_claim_rows("partner_demo_catalog.retail.orders", row_count=50)
    )

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if "extensions_synced" in sql:
            return _anchor_rows()
        if "workspace_claims_current_synced" in sql:
            return claim_rows
        return []

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)

    out = bridge.list_workspace_build_suggestions(user_id="u", limit=10)

    assert out["indexer_state"] == "active"
    assert out["evidence_gate"]["profiled_table_count"] == 3
    assert out["evidence_gate"]["profiled_schema_count"] == 2
    banking = next(
        item for item in out["suggestions"]
        if item["target"]["schema_ref"] == "partner_demo_catalog.banking"
    )
    assert banking["template_id"] == "starter.schema-profile-quality"
    assert banking["target"]["table_count"] == 2
    assert banking["evidence_summary"]["row_count"] == 120
    assert banking["evidence_summary"]["table_count"] == 2
    assert "table_ref" not in banking["target"]
    assert {
        table["table_ref"] for table in banking["included_tables"]
    } == {
        "partner_demo_catalog.banking.transactions",
        "partner_demo_catalog.banking.customers",
    }
    assert "__materialization_mat_hidden_1" not in json.dumps(out["suggestions"])


def test_plan_and_build_schema_suggestion_creates_combined_view(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_active(monkeypatch)
    claim_rows = (
        _profile_claim_rows("partner_demo_catalog.banking.transactions", row_count=100)
        + _profile_claim_rows("partner_demo_catalog.banking.customers", row_count=20)
    )
    statements: list[str] = []

    def fake_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if "extensions_synced" in sql:
            return _anchor_rows()
        if "workspace_claims_current_synced" in sql:
            return claim_rows
        return []

    monkeypatch.setattr(bridge, "_query_all", fake_query_all)
    monkeypatch.setattr(databricks_sql, "execute_sql_statement", statements.append)

    suggestion_id = (
        "profile-quality-schema:partner-demo-catalog-banking"
    )
    out = bridge.plan_and_build_workspace_suggestion(
        user_id="u", suggestion_id=suggestion_id,
    )

    assert out["status"] == "built"
    assert out["target"]["schema_ref"] == "partner_demo_catalog.banking"
    assert out["artifact"]["kind"] == "uc_view"
    assert out["artifact"]["name"].startswith("bv_schema_profile_quality_")
    view_sql = next(stmt for stmt in statements if "CREATE OR REPLACE VIEW" in stmt)
    assert "schema_ref" in view_sql
    assert "GROUP BY subject" in view_sql
    assert "'table:partner_demo_catalog.banking.transactions'" in view_sql
    assert "'table:partner_demo_catalog.banking.customers'" in view_sql


# ---------------------------------------------------------------------------
# Schema name comes from BV_SCHEMA env (and is sanitized)
# ---------------------------------------------------------------------------


def test_bv_schema_defaults_to_brickvision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BV_SCHEMA", raising=False)
    assert bridge._bv_schema() == "brickvision"


def test_bv_schema_picks_up_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BV_SCHEMA", "bv_demo")
    assert bridge._bv_schema() == "bv_demo"


def test_bv_schema_unsafe_value_blocks_query(
    configured_lakebase: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured ``BV_SCHEMA`` containing dots / quotes / spaces
    must never reach the SQL string — the sanitizer raises ValueError
    inside the bridge function and the FastAPI error handler turns it
    into a structured 500. We assert the raise here so a bad env can't
    silently open a SQL-identifier injection vector."""

    monkeypatch.setenv("BV_SCHEMA", "brickvision; DROP TABLE foo;")

    with pytest.raises(ValueError):
        bridge.list_top_orders(user_id="u")
