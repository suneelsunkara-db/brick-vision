from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SRC))

from brickvision_runtime.evidence_quality import validate_workspace_claim_quality
from brickvision_runtime.kg.claims import read_claims_from_dry_run_log
from brickvision_runtime.kg.retriever import kg_search
from brickvision_runtime.kg.schemas import ALL_DDL, render
from brickvision_runtime.skills.uc_catalog_introspect import (
    _build_claims,
    run_uc_catalog_introspect,
)


def test_workspace_claims_ddl_renders_flat_schema() -> None:
    ddl = render(ALL_DDL["workspace_claims"], "brickvision", "brickvision")
    current_ddl = render(
        ALL_DDL["workspace_claims_current"], "brickvision", "brickvision",
    )

    assert "CREATE TABLE IF NOT EXISTS brickvision.brickvision.workspace_claims" in ddl
    assert "workspace_profile_id STRING NOT NULL" in ddl
    assert "PARTITIONED BY (workspace_profile_id)" in ddl
    assert (
        "CREATE TABLE IF NOT EXISTS brickvision.brickvision.workspace_claims_current"
        in current_ddl
    )
    assert "'brickvision.role' = 'workspace_kg_claims_current'" in current_ddl


def test_uc_catalog_introspect_emits_claims_and_kg_search_finds_table(
    monkeypatch, tmp_path,
) -> None:
    fixture = REPO_ROOT / "tests" / "fixtures" / "kg" / "uc_introspection.json"
    claim_log = tmp_path / "last_emit_claims.json"

    monkeypatch.setenv("BV_DRY_RUN", "true")
    monkeypatch.setenv("BV_DRY_RUN_UC_INTROSPECTION_PATH", str(fixture))
    monkeypatch.setenv("BV_DRY_RUN_KG_CLAIMS_LOG", str(claim_log))
    monkeypatch.setenv("BV_ACTIVE_WORKSPACE_PROFILE", "partner-dev")

    result = run_uc_catalog_introspect(
        workspace_id="123456789",
        config_hash="cfg-sha",
        run_id="run-1",
    )

    assert result.catalogs_seen == 1
    assert result.schemas_seen == 1
    assert result.tables_seen == 1
    assert result.tables_profiled == 1
    assert result.claims_emitted == 11
    assert result.dry_run is True

    claims = read_claims_from_dry_run_log(claim_log)
    subjects = {claim["subject"] for claim in claims}
    assert "catalog:main" in subjects
    assert "schema:main.default" in subjects
    assert "table:main.default.customers" in subjects
    assert all(claim["workspace_profile_id"] == "partner-dev" for claim in claims)
    assert all(claim["claim_id"].startswith("wkg:") for claim in claims)

    refs = kg_search(query="customers table", claims=claims, k=5)
    assert refs
    assert refs[0].subject == "table:main.default.customers"


def test_uc_catalog_introspect_dry_run_payload_is_replay_hashable(
    monkeypatch, tmp_path,
) -> None:
    fixture = REPO_ROOT / "tests" / "fixtures" / "kg" / "uc_introspection.json"
    first_log = tmp_path / "first.json"
    second_log = tmp_path / "second.json"

    monkeypatch.setenv("BV_DRY_RUN", "true")
    monkeypatch.setenv("BV_DRY_RUN_UC_INTROSPECTION_PATH", str(fixture))
    monkeypatch.setenv("BV_ACTIVE_WORKSPACE_PROFILE", "partner-dev")

    monkeypatch.setenv("BV_DRY_RUN_KG_CLAIMS_LOG", str(first_log))
    run_uc_catalog_introspect(workspace_id="123456789")
    first_claim_ids = [
        row["claim_id"]
        for row in json.loads(first_log.read_text(encoding="utf-8"))["claims"]
    ]

    monkeypatch.setenv("BV_DRY_RUN_KG_CLAIMS_LOG", str(second_log))
    run_uc_catalog_introspect(workspace_id="123456789")
    second_claim_ids = [
        row["claim_id"]
        for row in json.loads(second_log.read_text(encoding="utf-8"))["claims"]
    ]

    assert first_claim_ids == second_claim_ids


def test_uc_catalog_introspect_uses_subject_specific_kinds() -> None:
    claims = _build_claims(
        workspace_profile_id="partner-dev",
        workspace_id="123456789",
        observed_at_ms=1,
        config_hash="cfg-sha",
        run_id="run-1",
        catalogs=[],
        schemas=[],
        tables=[],
        views=[
            {
                "table_catalog": "main",
                "table_schema": "default",
                "table_name": "v_customers",
            }
        ],
        volumes=[
            {
                "volume_catalog": "main",
                "volume_schema": "default",
                "volume_name": "documents",
            }
        ],
        functions=[
            {
                "routine_catalog": "main",
                "routine_schema": "default",
                "routine_name": "mask_email",
            }
        ],
    )

    by_subject = {claim.subject: claim.subject_kind for claim in claims}
    assert by_subject == {
        "view:main.default.v_customers": "VIEW",
        "volume:main.default.documents": "VOLUME",
        "function:main.default.mask_email": "FUNCTION",
    }


def test_uc_catalog_introspect_emits_profile_claims_for_tables() -> None:
    claims = _build_claims(
        workspace_profile_id="partner-dev",
        workspace_id="123456789",
        observed_at_ms=1,
        config_hash="cfg-sha",
        run_id="run-1",
        catalogs=[],
        schemas=[],
        tables=[],
        views=[],
        volumes=[],
        functions=[],
        table_profiles=[
            {
                "table_catalog": "main",
                "table_schema": "default",
                "table_name": "customers",
                "row_count": 3,
                "columns": [
                    {
                        "column_name": "customer_id",
                        "data_type": "BIGINT",
                        "is_nullable": "NO",
                        "ordinal_position": 1,
                    },
                    {
                        "column_name": "email",
                        "data_type": "STRING",
                        "is_nullable": "YES",
                        "ordinal_position": 2,
                    },
                ],
                "null_counts": {"customer_id": 0, "email": 1},
                "distinct_counts": {"customer_id": 3, "email": 2},
            }
        ],
    )

    predicates = {claim.predicate for claim in claims}
    assert {
        "ROW_COUNT",
        "HAS_COLUMN",
        "NULL_COUNT",
        "DISTINCT_COUNT",
        "GRAIN_CHECK",
    } <= predicates

    report = validate_workspace_claim_quality(
        claims=[
            {
                "subject": claim.subject,
                "subject_kind": claim.subject_kind,
                "predicate": claim.predicate,
                "source_skill_id": claim.source_skill_id,
            }
            for claim in claims
        ],
    )
    assert report.passed is True
