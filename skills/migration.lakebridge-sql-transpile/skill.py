"""Mechanical skill: ``skill:migration.lakebridge-sql-transpile``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:migration.lakebridge-sql-transpile",
    version="0.1.0",
    dag=DAG(name="migration.lakebridge-sql-transpile"),
    constitutional=(
        "migration.output.must.be.artifact.bundle",
        "materialization.must.be.optional.validation",
        "unsupported.syntax.must.be.reported",
    ),
)


def run_migration_lakebridge_sql_transpile(
    *,
    source_sql: str,
    raw_databricks_sql: str,
    remediated_databricks_sql: str,
    transpile_report: dict[str, Any],
    raw_validation: dict[str, Any] | None = None,
    build_validation: dict[str, Any] | None = None,
    proof_summary: dict[str, Any] | None = None,
    artifact_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a Lakebridge migration artifact bundle without materializing by default."""

    report = dict(transpile_report or {})
    summary = dict(proof_summary or {})
    lineage = report.get("lineage") if isinstance(report.get("lineage"), list) else []
    target_attempts = (
        report.get("target_dialect_attempts")
        if isinstance(report.get("target_dialect_attempts"), list)
        else []
    )
    databricks_attempt = next(
        (
            item
            for item in target_attempts
            if isinstance(item, dict) and item.get("target_dialect") == "databricks"
        ),
        {},
    )
    raw_status = ((raw_validation or {}).get("raw_mapped_explain") or {}).get("status")
    status = "transpilation_proven" if databricks_attempt.get("success_count") else "transpilation_failed"
    if raw_status == "failed":
        status = "transpilation_validation_failed"
    elif raw_status == "not_supported_by_cli" and databricks_attempt.get("success_count"):
        status = "transpilation_completed"
    artifact = {
        "artifact_kind": "migration_transpile_artifact",
        "source_dialect": report.get("source_dialect") or "unknown",
        "target_dialect": "databricks",
        "source_sql": source_sql,
        "raw_databricks_sql": raw_databricks_sql,
        "remediated_databricks_sql": remediated_databricks_sql,
        "lineage": [
            {"source": str(pair[0]), "target": str(pair[1])}
            for pair in lineage
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        ],
        "compatibility_report": {
            "parse": report.get("parse"),
            "success_count": databricks_attempt.get("success_count", 0),
            "error_count": databricks_attempt.get("error_count", 0),
            "errors": databricks_attempt.get("errors", []),
            "raw_validation_status": raw_status,
            "raw_validation_error": ((raw_validation or {}).get("raw_mapped_explain") or {}).get("error"),
            "remediation_status": summary.get("status"),
            "validation": summary.get("validation") or build_validation,
        },
        "artifact_paths": dict(artifact_paths or {}),
        "optional_validation_object": summary.get("built_artifact"),
    }
    return {
        "status": status,
        "executed": status in {"transpilation_proven", "transpilation_completed"},
        "proof_kind": "migration_transpile_artifact",
        "skill_id": "skill:migration.lakebridge-sql-transpile",
        "migration_artifact": artifact,
        "message": (
            "Lakebridge SQL transpilation artifact bundle is ready for review."
            if status == "transpilation_proven"
            else "Lakebridge Switch SQL transpilation completed; validation is not exposed by the installed llm-transpile CLI."
            if status == "transpilation_completed"
            else (
                "Lakebridge SQL transpilation produced raw output, but Databricks SQL validation failed."
                if status == "transpilation_validation_failed"
                else "Lakebridge SQL transpilation did not produce a Databricks artifact."
            )
        ),
    }


__all__ = ["SKILL", "run_migration_lakebridge_sql_transpile"]
