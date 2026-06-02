from __future__ import annotations

from brickvision_runtime.evidence_quality import (
    validate_skill_anchor_resolution,
    validate_workspace_claim_quality,
)


def test_skill_anchor_resolution_flags_missing_active_extensions() -> None:
    report = validate_skill_anchor_resolution(
        skill_exemplars={
            "delta.sql-transform": "meta:lakeflow/ext:sql-transform",
            "uc.catalog-introspect": "meta:uc/ext:list-catalogs",
        },
        extension_ids={"meta:uc/ext:list-catalogs"},
    )

    assert report.passed is False
    assert report.reason_codes == ("HAND_AUTHORED_SKILL_ANCHOR_NOT_IN_ACTIVE_GRAPH",)
    assert report.details["resolved_count"] == 1
    assert report.details["missing"] == [
        {
            "skill_id": "delta.sql-transform",
            "anchor": "meta:lakeflow/ext:sql-transform",
        },
    ]


def test_skill_anchor_resolution_allows_explicit_pending_stubs() -> None:
    report = validate_skill_anchor_resolution(
        skill_exemplars={
            "delta.sql-transform": "meta:lakeflow/ext:sql-transform",
        },
        extension_ids=set(),
        pending_stub_anchors={"meta:lakeflow/ext:sql-transform"},
    )

    assert report.passed is True
    assert report.details["pending_stub_count"] == 1
    assert report.details["missing_count"] == 0


def test_skill_anchor_resolution_flags_hand_authored_only_anchors_when_grounding_required() -> None:
    report = validate_skill_anchor_resolution(
        skill_exemplars={
            "lakeflow.jobs-run-submit": "meta:lakeflow-jobs/ext:jobs-runs-submit",
            "uc.catalog-introspect": "meta:unity-catalog-foundation/ext:list-catalogs",
        },
        extension_ids={
            "meta:lakeflow-jobs/ext:jobs-runs-submit",
            "meta:unity-catalog-foundation/ext:list-catalogs",
        },
        extension_source_kinds={
            "meta:lakeflow-jobs/ext:jobs-runs-submit": {"hand_authored"},
            "meta:unity-catalog-foundation/ext:list-catalogs": {"sdk", "hand_authored"},
        },
        require_source_grounding=True,
    )

    assert report.passed is False
    assert report.reason_codes == ("HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED",)
    assert report.details["ungrounded_count"] == 1
    assert report.details["ungrounded"] == [
        {
            "skill_id": "lakeflow.jobs-run-submit",
            "anchor": "meta:lakeflow-jobs/ext:jobs-runs-submit",
            "source_kinds": ["hand_authored"],
        },
    ]


def test_workspace_claim_quality_flags_shallow_profiles_and_kind_mismatch() -> None:
    report = validate_workspace_claim_quality(
        claims=[
            {
                "subject": "catalog:partner_demo_catalog",
                "subject_kind": "CATALOG",
                "predicate": "EXISTS",
                "source_skill_id": "skill:uc.catalog-introspect",
            },
            {
                "subject": "view:partner_demo_catalog.sales.v_orders",
                "subject_kind": "TABLE",
                "predicate": "BELONGS_TO",
                "source_skill_id": "skill:uc.catalog-introspect",
            },
        ],
    )

    assert report.passed is False
    assert set(report.reason_codes) == {
        "WORKSPACE_CLAIM_SUBJECT_KIND_MISMATCH",
        "WORKSPACE_PROFILE_CLAIMS_MISSING",
    }
    assert report.details["subject_kind_mismatch_count"] == 1
    assert "ROW_COUNT" in report.details["missing_profile_predicates"]


def test_workspace_claim_quality_passes_with_profile_claims() -> None:
    report = validate_workspace_claim_quality(
        claims=[
            {
                "subject": "table:partner_demo_catalog.sales.orders",
                "subject_kind": "TABLE",
                "predicate": "ROW_COUNT",
                "source_skill_id": "skill:delta.table-introspect",
            },
            {
                "subject": "table:partner_demo_catalog.sales.orders",
                "subject_kind": "TABLE",
                "predicate": "HAS_COLUMN",
                "source_skill_id": "skill:delta.table-introspect",
            },
            {
                "subject": "table:partner_demo_catalog.sales.orders",
                "subject_kind": "TABLE",
                "predicate": "NULL_COUNT",
                "source_skill_id": "skill:delta.table-introspect",
            },
            {
                "subject": "table:partner_demo_catalog.sales.orders",
                "subject_kind": "TABLE",
                "predicate": "DISTINCT_COUNT",
                "source_skill_id": "skill:delta.table-introspect",
            },
            {
                "subject": "table:partner_demo_catalog.sales.orders",
                "subject_kind": "TABLE",
                "predicate": "GRAIN_CHECK",
                "source_skill_id": "skill:delta.table-introspect",
            },
        ],
    )

    assert report.passed is True
    assert report.reason_codes == ()
