"""Mechanical skill: ``skill:migration.lakebridge-assess``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:migration.lakebridge-assess",
    version="0.1.0",
    dag=DAG(name="migration.lakebridge-assess"),
    constitutional=(
        "assessment.must_not_fabricate_analyzer_output",
        "support.must_be_evidence_backed",
        "blocked.state.must_explain_missing_bindings",
    ),
)


def run_migration_lakebridge_assess(
    *,
    source_system: str | None = None,
    source_path: str | None = None,
    assessment_output_path: str | None = None,
    lakebridge_env_path: str | None = None,
) -> dict[str, Any]:
    """Build a Lakebridge assessment readiness artifact."""

    normalized_source = _normalize(
        source_system
        or os.environ.get("BV_LAKEBRIDGE_ASSESSMENT_SOURCE_SYSTEM")
        or os.environ.get("BV_SQL_TRANSPILE_SOURCE_DIALECT")
        or "teradata"
    )
    effective_source_path = _optional_string(source_path) or _optional_string(
        os.environ.get("BV_LAKEBRIDGE_ASSESSMENT_SOURCE_PATH")
    )
    effective_output_path = _optional_string(assessment_output_path) or _optional_string(
        os.environ.get("BV_LAKEBRIDGE_ASSESSMENT_OUTPUT_PATH")
    )
    package_root = _resolve_package_root(lakebridge_env_path)
    assessment = _assessment_evidence(package_root)
    support_matrix = _support_matrix(normalized_source, assessment)
    checks = [
        _check(
            "lakebridge_package",
            package_root is not None,
            "Lakebridge package resources are available.",
            "Install or point lakebridge_env_path to Lakebridge site-packages.",
            {"package_root": str(package_root) if package_root else ""},
        ),
        _check(
            "assessment_resources",
            assessment["status"] == "declared",
            "Lakebridge assessment source-system declarations were observed.",
            "Lakebridge assessment constants were not found in the installed package.",
            assessment,
        ),
        _check(
            "source_system",
            not assessment["source_systems"] or normalized_source in assessment["source_systems"],
            f"{normalized_source} is declared for Lakebridge assessment.",
            f"{normalized_source} was not observed in Lakebridge assessment support declarations.",
            {"source_system": normalized_source, "observed_source_systems": assessment["source_systems"]},
        ),
        _check(
            "source_path",
            _is_volume_path(effective_source_path),
            "Assessment source path is bound to a UC Volume.",
            "Bind source_path to /Volumes/<catalog>/<schema>/<volume>/... before running a real assessment.",
            {"value": effective_source_path or ""},
        ),
    ]
    if effective_output_path:
        checks.append(
            _check(
                "assessment_output_path",
                _is_volume_path(effective_output_path),
                "Assessment output path is bound to a UC Volume.",
                "Bind assessment_output_path to /Volumes/<catalog>/<schema>/<volume>/...",
                {"value": effective_output_path},
            )
        )
    blockers = [check for check in checks if check["status"] == "blocked"]
    ready = not blockers
    artifact = {
        "source_system": normalized_source,
        "source_path": effective_source_path or "",
        "assessment_output_path": effective_output_path or "",
        "phase": "assessment",
        "inventory_status": "not_collected",
        "complexity_status": "not_collected",
        "support_status": "supported" if support_matrix else "not_observed",
        "blockers": blockers,
        "next_workflows": _next_workflows(normalized_source, ready=ready),
    }
    return {
        "status": "assessment_ready" if ready else "assessment_blocked",
        "executed": ready,
        "proof_kind": "lakebridge_assessment_readiness",
        "skill_id": "skill:migration.lakebridge-assess",
        "assessment_artifact": artifact,
        "readiness_checks": checks,
        "support_matrix": support_matrix,
        "message": (
            "Lakebridge assessment readiness checks passed. A real Analyzer run can be wired next."
            if ready
            else "Lakebridge assessment is blocked until readiness checks pass."
        ),
    }


def _resolve_package_root(configured: str | None) -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.extend(
            [
                parent / ".lakebridge-venv" / "lib" / "python3.13" / "site-packages",
                parent / ".lakebridge-venv" / "lib" / "python3.12" / "site-packages",
            ]
        )
    for candidate in candidates:
        if (candidate / "databricks" / "labs").exists():
            return candidate
    return None


def _assessment_evidence(package_root: Path | None) -> dict[str, Any]:
    if package_root is None:
        return {"source_systems": [], "evidence": [], "status": "not_observed"}
    constants = package_root / "databricks" / "labs" / "lakebridge" / "assessments" / "_constants.py"
    systems: list[str] = []
    evidence: list[str] = []
    if constants.exists():
        text = constants.read_text(encoding="utf-8", errors="replace")
        systems = [_normalize(item) for item in _list_literal(text, "PROFILER_SOURCE_SYSTEM")]
        evidence.append(str(constants))
    return {
        "source_systems": systems,
        "evidence": evidence,
        "status": "declared" if systems else "not_observed",
    }


def _support_matrix(source_system: str, assessment: dict[str, Any]) -> list[dict[str, Any]]:
    if source_system not in set(assessment.get("source_systems") or []):
        return []
    return [
        {
            "source_system": source_system,
            "assessment": "declared_by_profiler",
            "confidence": "declared",
            "evidence": list(assessment.get("evidence") or []),
        }
    ]


def _next_workflows(source_system: str, *, ready: bool) -> list[dict[str, str]]:
    if not ready:
        return [{"workflow_type": "assessment", "status": "fix_readiness_blockers"}]
    return [
        {"workflow_type": "sql_transpile", "status": "available_if_sql_assets_exist"},
        {"workflow_type": "code_convert", "status": "available_if_code_assets_exist"},
        {"workflow_type": "reconcile", "status": "build_next"},
        {"workflow_type": "assessment", "status": f"{source_system}_readiness_checked"},
    ]


def _check(
    name: str,
    passed: bool,
    passed_message: str,
    blocked_message: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "passed" if passed else "blocked",
        "message": passed_message if passed else blocked_message,
        "details": details,
    }


def _is_volume_path(value: str | None) -> bool:
    return bool(value and value.startswith("/Volumes/") and len(value.split("/")) >= 5)


def _optional_string(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else ""


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _list_literal(text: str, name: str) -> list[str]:
    marker = f"{name} = ["
    start = text.find(marker)
    if start < 0:
        return []
    end = text.find("]", start)
    if end < 0:
        return []
    raw = text[start + len(marker):end]
    return [part.strip().strip("'\"") for part in raw.split(",") if part.strip()]
