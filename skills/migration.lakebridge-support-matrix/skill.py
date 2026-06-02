"""Mechanical skill: ``skill:migration.lakebridge-support-matrix``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:migration.lakebridge-support-matrix",
    version="0.1.0",
    dag=DAG(name="migration.lakebridge-support-matrix"),
    constitutional=(
        "support.must.be.evidence_backed",
        "prompt_presence.must.not_equal_operational_support",
        "unsupported.systems.must_be_reported_as_unknown_not_failed",
    ),
)


def run_migration_lakebridge_support_matrix(
    *,
    lakebridge_env_path: str | None = None,
    source_system: str | None = None,
) -> dict[str, Any]:
    """Return observed Lakebridge/Switch support by source system and phase."""

    package_root = _resolve_package_root(lakebridge_env_path)
    evidence = {
        "package_root": str(package_root) if package_root else "",
        "assessment": _assessment_evidence(package_root),
        "reconcile": _reconcile_evidence(package_root),
        "switch": _switch_evidence(package_root),
        "deterministic_transpile": _deterministic_transpile_evidence(package_root),
    }
    systems = _systems_from_evidence(evidence)
    if source_system:
        wanted = _normalize(source_system)
        systems = {key: value for key, value in systems.items() if key == wanted}
    matrix = [_system_row(system, phases) for system, phases in sorted(systems.items())]
    gaps = _recommended_gaps(matrix)
    return {
        "status": "support_matrix_ready",
        "executed": True,
        "proof_kind": "lakebridge_support_matrix",
        "skill_id": "skill:migration.lakebridge-support-matrix",
        "support_matrix": matrix,
        "recommended_skill_gaps": gaps,
        "evidence": evidence,
        "message": (
            "Lakebridge support matrix was built from installed package resources. "
            "Prompt-level support is not treated as operational proof."
        ),
    }


def _resolve_package_root(configured: str | None) -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / ".lakebridge-venv" / "lib" / "python3.13" / "site-packages")
    candidates.extend(
        [
            Path.cwd() / ".lakebridge-venv" / "lib" / "python3.13" / "site-packages",
            Path.cwd() / "site-packages",
        ]
    )
    for candidate in candidates:
        labs = candidate / "databricks" / "labs"
        if labs.exists():
            return candidate
    return None


def _assessment_evidence(package_root: Path | None) -> dict[str, Any]:
    if package_root is None:
        return {"source_systems": [], "evidence": [], "status": "not_observed"}
    constants = package_root / "databricks" / "labs" / "lakebridge" / "assessments" / "_constants.py"
    systems = []
    evidence = []
    if constants.exists():
        text = constants.read_text(encoding="utf-8", errors="replace")
        systems = _list_literal(text, "PROFILER_SOURCE_SYSTEM")
        evidence.append(str(constants))
    return {
        "source_systems": [_normalize(item) for item in systems],
        "evidence": evidence,
        "status": "declared" if systems else "not_observed",
    }


def _reconcile_evidence(package_root: Path | None) -> dict[str, Any]:
    if package_root is None:
        return {"source_systems": [], "connectors": [], "evidence": [], "status": "not_observed"}
    constants = package_root / "databricks" / "labs" / "lakebridge" / "reconcile" / "constants.py"
    manager = package_root / "databricks" / "labs" / "lakebridge" / "connections" / "database_manager.py"
    systems: list[str] = []
    connectors: list[str] = []
    evidence: list[str] = []
    if constants.exists():
        text = constants.read_text(encoding="utf-8", errors="replace")
        systems = _enum_members(text, "ReconSourceType")
        evidence.append(str(constants))
    if manager.exists():
        text = manager.read_text(encoding="utf-8", errors="replace")
        connectors = _connector_keys(text)
        evidence.append(str(manager))
    return {
        "source_systems": [_normalize(item) for item in systems],
        "connectors": [_normalize(item) for item in connectors],
        "evidence": evidence,
        "status": "declared" if systems else "not_observed",
    }


def _switch_evidence(package_root: Path | None) -> dict[str, Any]:
    if package_root is None:
        return {"sql_prompts": [], "code_prompts": [], "etl_prompts": [], "workflow_prompts": [], "status": "not_observed"}
    prompts = package_root / "databricks" / "labs" / "switch" / "resources" / "builtin_prompts"
    sql_dir = prompts / "sql_to_databricks_python_notebook"
    code_dir = prompts / "code_to_databricks_python_notebook"
    etl_dir = prompts / "etl_to_lakeflow_sdp"
    workflow_dir = prompts / "workflow_to_databricks_jobs"
    return {
        "sql_prompts": _prompt_names(sql_dir),
        "code_prompts": _prompt_names(code_dir),
        "etl_prompts": _prompt_names(etl_dir),
        "workflow_prompts": _prompt_names(workflow_dir),
        "evidence": [str(path) for path in (sql_dir, code_dir, etl_dir, workflow_dir) if path.exists()],
        "status": "observed" if prompts.exists() else "not_observed",
    }


def _deterministic_transpile_evidence(package_root: Path | None) -> dict[str, Any]:
    if package_root is None:
        return {"dialects": [], "status": "not_observed", "evidence": []}
    repo = package_root / "databricks" / "labs" / "lakebridge" / "transpiler" / "repository.py"
    installers = package_root / "databricks" / "labs" / "lakebridge" / "transpiler" / "installers.py"
    evidence = [str(path) for path in (repo, installers) if path.exists()]
    # Installed deterministic dialects live under the user's Lakebridge repository at runtime.
    # This skill reports package-observed capability only, not a fabricated installed list.
    return {"dialects": [], "status": "requires_runtime_repository_probe", "evidence": evidence}


def _systems_from_evidence(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    systems: dict[str, dict[str, Any]] = {}

    def row(system: str) -> dict[str, Any]:
        key = _normalize(system)
        return systems.setdefault(
            key,
            {
                "assessment": "not_observed",
                "sql_transpile": "not_observed",
                "code_convert": "not_observed",
                "workflow_convert": "not_observed",
                "reconcile": "not_observed",
                "operational_connector": "not_observed",
                "evidence": [],
            },
        )

    assessment = evidence["assessment"]
    for system in assessment.get("source_systems", []):
        item = row(system)
        item["assessment"] = "declared_by_profiler"
        item["evidence"].extend(assessment.get("evidence", []))

    switch = evidence["switch"]
    for system in switch.get("sql_prompts", []):
        item = row(system)
        item["sql_transpile"] = "supported_by_switch_prompt"
        item["evidence"].extend(switch.get("evidence", []))
    for system in switch.get("code_prompts", []):
        item = row(system)
        item["code_convert"] = "supported_by_switch_prompt"
        item["evidence"].extend(switch.get("evidence", []))
    for system in switch.get("etl_prompts", []):
        item = row(system)
        item["code_convert"] = "supported_by_switch_etl_prompt"
        item["evidence"].extend(switch.get("evidence", []))
    for system in switch.get("workflow_prompts", []):
        item = row(system)
        item["workflow_convert"] = "supported_by_switch_prompt"
        item["evidence"].extend(switch.get("evidence", []))

    reconcile = evidence["reconcile"]
    for system in reconcile.get("source_systems", []):
        item = row(system)
        item["reconcile"] = "declared_by_reconcile_enum"
        item["evidence"].extend(reconcile.get("evidence", []))
    for system in reconcile.get("connectors", []):
        item = row(system)
        item["operational_connector"] = "connector_factory_present"
        item["evidence"].extend(reconcile.get("evidence", []))

    return systems


def _system_row(system: str, phases: dict[str, Any]) -> dict[str, Any]:
    evidence = sorted(set(str(item) for item in phases.get("evidence", []) if item))
    confidence = "operational" if phases.get("operational_connector") == "connector_factory_present" else "observed"
    if any(str(value).startswith("supported_by_switch_prompt") for value in phases.values()):
        confidence = "prompt_observed"
    return {
        "source_system": system,
        "assessment": phases["assessment"],
        "sql_transpile": phases["sql_transpile"],
        "code_convert": phases["code_convert"],
        "workflow_convert": phases["workflow_convert"],
        "reconcile": phases["reconcile"],
        "operational_connector": phases["operational_connector"],
        "confidence": confidence,
        "evidence": evidence[:8],
    }


def _recommended_gaps(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if any(row["assessment"] == "declared_by_profiler" for row in matrix):
        gaps.append(
            {
                "missing_skill_id": "skill:migration.lakebridge-assess",
                "reason": "Assessment support is declared, but BrickVision does not yet expose an assessment workflow skill.",
                "priority": "high",
            }
        )
    if any(row["reconcile"] == "declared_by_reconcile_enum" for row in matrix):
        gaps.append(
            {
                "missing_skill_id": "skill:migration.lakebridge-reconcile",
                "reason": "Reconcile source types are declared, but BrickVision does not yet expose a reconcile workflow skill.",
                "priority": "high",
            }
        )
    return gaps


def _prompt_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(item.stem.lower() for item in path.glob("*.yml"))


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


def _enum_members(text: str, class_name: str) -> list[str]:
    start = text.find(f"class {class_name}")
    if start < 0:
        return []
    body = text[start:].split("\n")
    members: list[str] = []
    for line in body[1:]:
        if line.startswith("class "):
            break
        stripped = line.strip()
        if " = auto()" in stripped:
            members.append(stripped.split("=", 1)[0].strip())
    return members


def _connector_keys(text: str) -> list[str]:
    start = text.find("connectors = {")
    if start < 0:
        return []
    end = text.find("}", start)
    raw = text[start:end]
    keys: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if ":" in stripped and stripped[0:1] in {"'", '"'}:
            keys.append(stripped.split(":", 1)[0].strip().strip("'\""))
    return keys
