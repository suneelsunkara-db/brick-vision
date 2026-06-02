"""Plan Usecase orchestration."""

from __future__ import annotations

from typing import Any

from .usecase_artifacts import (
    _build_plan_id,
    _execute_build_view,
    _persist_build_plan,
    _schema_profile_quality_sql,
    _schema_profile_quality_view_name,
)
from .usecase_suggestions import list_workspace_build_suggestions

def plan_and_build_workspace_suggestion(
    *, user_id: str, suggestion_id: str,
) -> dict[str, Any]:
    """Persist, evidence-check, and execute one safe usecase starter artifact."""

    payload = list_workspace_build_suggestions(user_id=user_id, limit=50)
    suggestion = next(
        (
            item for item in payload.get("suggestions", [])
            if item.get("suggestion_id") == suggestion_id
        ),
        None,
    )
    if suggestion is None:
        return {
            "status": "not_found",
            "suggestion_id": suggestion_id,
            "message": (
                "Suggestion is not available under the current evidence gate. "
                "Refresh Workspace KG and Capability Graph, then retry."
            ),
        }

    subject = str(suggestion["target"]["subject"])
    schema_ref = str(suggestion["target"]["schema_ref"])
    table_subjects = [
        str(table["subject"])
        for table in suggestion.get("included_tables", [])
        if table.get("subject")
    ]
    view_name = _schema_profile_quality_view_name(schema_ref)
    artifact_sql = _schema_profile_quality_sql(
        schema_ref=schema_ref,
        table_subjects=table_subjects,
        view_name=view_name,
    )
    plan_payload = _usecase_plan_payload(
        suggestion=suggestion,
        suggestion_id=suggestion_id,
        active_snapshot_id=payload.get("active_snapshot_id"),
        artifact_sql=artifact_sql,
        view_name=view_name,
    )
    _persist_build_plan(
        plan=plan_payload,
        user_id=user_id,
        execution_status="planned",
        execution_result=None,
    )

    fresh_payload = list_workspace_build_suggestions(user_id=user_id, limit=50)
    still_valid = any(
        item.get("suggestion_id") == suggestion_id
        and item.get("target", {}).get("subject") == subject
        for item in fresh_payload.get("suggestions", [])
    )
    if not still_valid or fresh_payload.get("active_snapshot_id") != payload.get(
        "active_snapshot_id"
    ):
        blocked = {
            **plan_payload,
            "status": "blocked_evidence_drift",
            "execution_result": {
                "executed": False,
                "reason": "Evidence changed between plan and execution.",
            },
        }
        _persist_build_plan(
            plan=blocked,
            user_id=user_id,
            execution_status="blocked_evidence_drift",
            execution_result=blocked["execution_result"],
        )
        return blocked

    execution_result = _execute_build_view(artifact_sql, view_name=view_name)
    status = "built" if execution_result["executed"] else "build_failed"
    result = {**plan_payload, "status": status, "execution_result": execution_result}
    _persist_build_plan(
        plan=result,
        user_id=user_id,
        execution_status=status,
        execution_result=execution_result,
    )
    return result


def _usecase_plan_payload(
    *,
    suggestion: dict[str, Any],
    suggestion_id: str,
    active_snapshot_id: Any,
    artifact_sql: str,
    view_name: str,
) -> dict[str, Any]:
    plan_id = _build_plan_id(
        suggestion_id=suggestion_id,
        snapshot_id=str(active_snapshot_id or ""),
        artifact_sql=artifact_sql,
    )
    return {
        "status": "planned",
        "plan_id": plan_id,
        "suggestion_id": suggestion_id,
        "title": suggestion["title"],
        "active_snapshot_id": active_snapshot_id,
        "target": suggestion["target"],
        "build_plan": [
            {
                "step_id": "inspect-profile-claims",
                "skill_id": "skill:delta.table-introspect",
                "description": (
                    "Use existing Workspace KG table profile claims as the schema "
                    "build input; do not rescan the workspace from the UI path."
                ),
            },
            {
                "step_id": "generate-quality-view-sql",
                "skill_id": "skill:delta.sql-transform",
                "description": (
                    "Generate a SQL starter that combines relevant tables in the "
                    "schema into one quality and readiness view."
                ),
            },
            {
                "step_id": "validate-evidence-contract",
                "skill_id": "skill:uc.catalog-introspect",
                "description": (
                    "Block execution if required schema table profile claims "
                    "disappear or the target schema changes."
                ),
            },
        ],
        "artifact": {
            "kind": "uc_view",
            "name": view_name,
            "filename": view_name + ".sql",
            "sql": artifact_sql,
        },
        "evidence": suggestion["evidence"],
        "next_action": (
            "Open the generated UC view or use it as a starter artifact in a "
            "downstream usecase lifecycle."
        ),
    }



