"""Evidence-backed usecase suggestion compilation."""

from __future__ import annotations

import os
from typing import Any

from .usecase_artifacts import load_latest_build_plan_statuses
from .workspace_visibility import (
    INTERNAL_UC_TABLE_PREFIXES,
    workspace_claim_visibility_where,
)

REQUIRED_SUGGESTION_ANCHORS: dict[str, str] = {
    "skill:delta.table-introspect": "meta:delta-lake/ext:introspect-table-metadata",
    "skill:delta.sql-transform": (
        "meta:lakeflow-declarative-pipelines/ext:sql-transform-with-expectations"
    ),
    "skill:uc.catalog-introspect": (
        "meta:unity-catalog-foundation/ext:introspect-catalog-tree"
    ),
}

PROFILE_PREDICATES: tuple[str, ...] = (
    "ROW_COUNT",
    "HAS_COLUMN",
    "NULL_COUNT",
    "DISTINCT_COUNT",
    "GRAIN_CHECK",
)

REQUIRED_PROFILE_PREDICATES: tuple[str, ...] = (
    "HAS_COLUMN",
    "NULL_COUNT",
    "DISTINCT_COUNT",
)

def list_workspace_build_suggestions(*, user_id: str, limit: int = 12) -> dict[str, Any]:
    """Compile evidence-backed schema-level usecase suggestions."""

    b = _bridge()
    if not b._lakebase_configured():
        status = b._lakebase_config_status()
        return {
            "suggestions": [],
            "indexer_state": "lakebase_not_configured",
            "message": status["message"],
            "lakebase_config": status,
        }

    snapshot = b._active_snapshot()
    if snapshot is None:
        return {
            "suggestions": [],
            "indexer_state": "never_run",
            "message": b._INDEXER_NOT_RUN_BANNER["message"],
        }

    schema = b._sanitize_ident(b._bv_schema())
    anchor_rows = b._query_all(
        f"""
        SELECT extension_id, exemplar_skill_id, title
        FROM {schema}.extensions_synced
        WHERE snapshot_id = %s
          AND extension_id = ANY(%s)
        """,
        (snapshot[0], list(REQUIRED_SUGGESTION_ANCHORS.values())),
    )
    anchors = {
        str(row[0]): {
            "extension_id": str(row[0]),
            "skill_id": str(row[1]) if row[1] is not None else None,
            "title": str(row[2]) if row[2] is not None else "",
        }
        for row in anchor_rows
    }
    missing_anchors = [
        {"skill_id": skill_id, "extension_id": extension_id}
        for skill_id, extension_id in REQUIRED_SUGGESTION_ANCHORS.items()
        if extension_id not in anchors
    ]
    if missing_anchors:
        return {
            "suggestions": [],
            "indexer_state": "evidence_blocked",
            "message": "Required skill anchors are not resolved in the active graph.",
            "missing_capabilities": missing_anchors,
            "active_snapshot_id": snapshot[0],
        }

    claim_rows = b._query_all(
        f"""
        SELECT subject, predicate, object_ref, value_json, metadata_json, observed_at_ms
        FROM {schema}.workspace_claims_current_synced
        WHERE subject_kind = 'TABLE'
          AND {workspace_claim_visibility_where()}
          AND predicate = ANY(%s)
        ORDER BY subject ASC, predicate ASC, object_ref ASC
        """,
        (list(PROFILE_PREDICATES),),
    )
    profiles = _compile_table_profiles(claim_rows)
    ready_profiles = [
        profile for profile in profiles
        if _profile_is_suggestion_ready(profile)
    ]
    schema_profiles = _compile_schema_profiles(ready_profiles)
    suggestions = [
        _schema_profile_to_build_suggestion(schema_profile, anchors, snapshot[0])
        for schema_profile in schema_profiles
    ]
    suggestions.sort(
        key=lambda item: (
            -float(item["confidence"]),
            -int(item["evidence_summary"]["table_count"]),
            -int(item["evidence_summary"]["row_count"]),
            item["title"],
        )
    )

    if _status_overlay_enabled():
        build_statuses = load_latest_build_plan_statuses(
            [str(item["suggestion_id"]) for item in suggestions]
        )
        for suggestion in suggestions:
            latest_build = build_statuses.get(str(suggestion["suggestion_id"]))
            if latest_build:
                suggestion["latest_build"] = latest_build
                suggestion["status"] = str(
                    latest_build.get("status") or suggestion["status"]
                )

    return {
        "suggestions": suggestions[: max(1, min(int(limit), 50))],
        "active_snapshot_id": snapshot[0],
        "indexer_state": "active",
        "evidence_gate": {
            "passed": True,
            "queried_predicates": list(PROFILE_PREDICATES),
            "required_predicates": list(REQUIRED_PROFILE_PREDICATES),
            "required_capabilities": [
                {"skill_id": skill_id, "extension_id": extension_id}
                for skill_id, extension_id in REQUIRED_SUGGESTION_ANCHORS.items()
            ],
            "profiled_table_count": len(profiles),
            "ready_table_count": len(ready_profiles),
            "profiled_schema_count": len(schema_profiles),
            "suggestion_count": len(suggestions),
        },
    }


def _compile_table_profiles(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    b = _bridge()
    profiles: dict[str, dict[str, Any]] = {}
    for subject, predicate, object_ref, value_json, metadata_json, observed_at_ms in rows:
        subject_text = str(subject)
        table_ref = subject_text.removeprefix("table:")
        if _is_internal_uc_table_ref(table_ref):
            continue
        profile = profiles.setdefault(
            subject_text,
            {
                "subject": subject_text,
                "table_ref": table_ref,
                "row_count": None,
                "columns": {},
                "null_counts": {},
                "distinct_counts": {},
                "grain": {},
                "predicates": set(),
                "observed_at_ms": 0,
            },
        )
        pred = str(predicate)
        profile["predicates"].add(pred)
        if observed_at_ms is not None:
            profile["observed_at_ms"] = max(
                int(profile["observed_at_ms"]), int(observed_at_ms),
            )
        value = b._decode_json(value_json) or {}
        metadata = b._decode_json(metadata_json) or {}
        if pred == "ROW_COUNT":
            profile["row_count"] = int(value.get("row_count", 0))
        elif pred == "HAS_COLUMN":
            column_name = _column_name_from_claim(value, object_ref)
            if column_name:
                profile["columns"][column_name] = value
        elif pred == "NULL_COUNT":
            column_name = str(value.get("column") or _column_name_from_ref(object_ref))
            if column_name:
                profile["null_counts"][column_name] = int(value.get("null_count", 0))
        elif pred == "DISTINCT_COUNT":
            column_name = str(value.get("column") or _column_name_from_ref(object_ref))
            if column_name:
                profile["distinct_counts"][column_name] = int(
                    value.get("distinct_count", 0)
                )
        elif pred == "GRAIN_CHECK":
            profile["grain"] = value
        if isinstance(metadata, dict) and metadata.get("columns"):
            for column in metadata.get("columns", []):
                if isinstance(column, dict):
                    column_name = str(column.get("column_name", ""))
                    if column_name:
                        profile["columns"].setdefault(column_name, column)
    return list(profiles.values())


def _profile_is_suggestion_ready(profile: dict[str, Any]) -> bool:
    predicates = profile["predicates"]
    return (
        all(predicate in predicates for predicate in REQUIRED_PROFILE_PREDICATES)
        and bool(profile["columns"])
        and bool(profile["null_counts"])
        and bool(profile["distinct_counts"])
    )


def _compile_schema_profiles(table_profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for profile in table_profiles:
        table_ref = str(profile["table_ref"])
        schema_ref = _schema_ref_from_table_ref(table_ref)
        if not schema_ref:
            continue
        schema_profile = schemas.setdefault(
            schema_ref,
            {
                "schema_ref": schema_ref,
                "subject": f"schema:{schema_ref}",
                "tables": [],
                "row_count": 0,
                "column_count": 0,
                "profiled_column_count": 0,
                "candidate_key_columns": set(),
                "null_risk_columns": set(),
                "observed_at_ms": 0,
            },
        )
        table_summary = _table_profile_summary(profile)
        schema_profile["tables"].append(table_summary)
        schema_profile["row_count"] += table_summary["row_count"]
        schema_profile["column_count"] += table_summary["column_count"]
        schema_profile["profiled_column_count"] += table_summary["profiled_column_count"]
        schema_profile["candidate_key_columns"].update(
            f"{table_summary['table_name']}.{column}"
            for column in table_summary["candidate_key_columns"]
        )
        schema_profile["null_risk_columns"].update(
            f"{table_summary['table_name']}.{column}"
            for column in table_summary["null_risk_columns"]
        )
        schema_profile["observed_at_ms"] = max(
            int(schema_profile["observed_at_ms"]),
            int(table_summary["observed_at_ms"]),
        )
    for schema_profile in schemas.values():
        schema_profile["tables"].sort(
            key=lambda item: (-int(item["row_count"]), item["table_ref"])
        )
    return list(schemas.values())


def _schema_profile_to_build_suggestion(
    schema_profile: dict[str, Any],
    anchors: dict[str, dict[str, Any]],
    snapshot_id: str,
) -> dict[str, Any]:
    schema_ref = str(schema_profile["schema_ref"])
    tables = list(schema_profile["tables"])
    table_count = len(tables)
    row_count = int(schema_profile.get("row_count") or 0)
    candidate_keys = sorted(schema_profile["candidate_key_columns"])[:12]
    null_risk_columns = sorted(schema_profile["null_risk_columns"])[:12]
    confidence = 0.72
    if table_count >= 2:
        confidence += 0.08
    if candidate_keys:
        confidence += 0.05
    if row_count >= 100:
        confidence += 0.05
    if int(schema_profile["profiled_column_count"]) >= 10:
        confidence += 0.03
    confidence = min(confidence, 0.92)
    catalog, schema_name = schema_ref.split(".", 1)
    return {
        "suggestion_id": "profile-quality-schema:" + _suggestion_slug(schema_ref),
        "template_id": "starter.schema-profile-quality",
        "title": f"Build a schema quality starter for {schema_ref}",
        "summary": (
            "Generate a starter usecase plan that combines relevant tables in this "
            "schema into row-count, null-count, distinct-count, and grain checks."
        ),
        "confidence": round(confidence, 2),
        "target": {
            "subject": schema_profile["subject"],
            "schema_ref": schema_ref,
            "catalog": catalog,
            "schema": schema_name,
            "table_count": table_count,
            "row_count": row_count,
        },
        "evidence_summary": {
            "row_count": row_count,
            "table_count": table_count,
            "column_count": int(schema_profile["column_count"]),
            "profiled_column_count": int(schema_profile["profiled_column_count"]),
            "candidate_key_columns": candidate_keys,
            "null_risk_columns": null_risk_columns,
            "observed_at_ms": int(schema_profile.get("observed_at_ms") or 0),
        },
        "included_tables": tables,
        "required_skills": sorted(REQUIRED_SUGGESTION_ANCHORS),
        "evidence": {
            "capability_anchors": [
                anchors[extension_id]
                for extension_id in REQUIRED_SUGGESTION_ANCHORS.values()
            ],
            "workspace_predicates": list(PROFILE_PREDICATES),
            "active_snapshot_id": snapshot_id,
        },
        "status": "ready_to_plan",
    }


def _table_profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    table_ref = str(profile["table_ref"])
    profiled_columns = sorted(
        set(profile["null_counts"]) | set(profile["distinct_counts"])
    )
    candidate_keys = [
        str(item)
        for item in (profile.get("grain") or {}).get("candidate_key_columns", [])
    ]
    null_risk_columns = [
        column for column, count in sorted(profile["null_counts"].items())
        if int(count) > 0
    ]
    return {
        "subject": profile["subject"],
        "table_ref": table_ref,
        "table_name": table_ref.rsplit(".", 1)[-1],
        "row_count": int(profile.get("row_count") or 0),
        "column_count": len(profile["columns"]),
        "profiled_column_count": len(profiled_columns),
        "candidate_key_columns": candidate_keys,
        "null_risk_columns": null_risk_columns[:8],
        "observed_at_ms": int(profile.get("observed_at_ms") or 0),
    }


def _is_internal_uc_table_ref(table_ref: str) -> bool:
    table_name = table_ref.rsplit(".", 1)[-1]
    return any(table_name.startswith(prefix) for prefix in INTERNAL_UC_TABLE_PREFIXES)


def _column_name_from_claim(value: Any, object_ref: Any) -> str:
    if isinstance(value, dict):
        raw = value.get("column_name") or value.get("column")
        if raw:
            return str(raw)
    return _column_name_from_ref(object_ref)


def _column_name_from_ref(object_ref: Any) -> str:  # noqa: ANN401
    if object_ref is None:
        return ""
    return str(object_ref).rsplit(".", 1)[-1]


def _schema_ref_from_table_ref(table_ref: str) -> str:
    parts = table_ref.split(".")
    if len(parts) < 3:
        return ""
    return ".".join(parts[:2])


def _suggestion_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _status_overlay_enabled() -> bool:
    return os.environ.get("BV_USECASE_STATUS_OVERLAY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }




def _bridge() -> Any:
    from . import runtime_bridge

    return runtime_bridge
