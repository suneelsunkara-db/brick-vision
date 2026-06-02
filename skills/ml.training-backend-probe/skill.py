"""Mechanical Layer-0 skill: ``skill:ml.training-backend-probe``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.training-backend-probe",
    version="0.1.0",
    dag=DAG(name="ml.training-backend-probe"),
    constitutional=(
        "backend.probe.must.not-train",
        "runtime.evidence.must.be-observed",
    ),
)


def run_ml_training_backend_probe(
    *,
    runtime_surface: str,
    probe_driver_uri: str | None = None,
    probe_output_table: str | None = None,
    probe_id: str | None = None,
    probe_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    runtime = str(runtime_surface or "").strip() or "unknown"
    runtime_evidence = _runtime_evidence_from_result(probe_result, runtime_surface=runtime)
    job_submit_body = None
    if runtime_evidence is None:
        driver_uri = str(probe_driver_uri or "").strip()
        output_table = str(probe_output_table or "").strip()
        probe_key = str(probe_id or "").strip()
        missing = [
            name
            for name, value in {
                "probe_driver_uri": driver_uri,
                "probe_output_table": output_table,
                "probe_id": probe_key,
            }.items()
            if not value
        ]
        if missing:
            findings.append(
                _finding(
                    "blocking",
                    "PROBE_BINDINGS_REQUIRED",
                    f"Missing backend probe bindings: {', '.join(sorted(missing))}.",
                )
            )
        elif not _valid_driver_uri(driver_uri):
            findings.append(
                _finding(
                    "blocking",
                    "PROBE_DRIVER_URI_REQUIRED",
                    "Bind an existing Workspace, Volumes, or DBFS backend probe driver artifact URI.",
                )
            )
        else:
            job_submit_body = {
                "run_name": f"brickvision-ml-backend-probe-{probe_key}",
                "environments": [
                    {
                        "environment_key": "default",
                        "spec": {
                            "client": "2",
                            "dependencies": ["databricks-sdk>=0.68", "mlflow"],
                        },
                    }
                ],
                "tasks": [
                    {
                        "task_key": "probe_ml_backends",
                        "environment_key": "default",
                        "spark_python_task": {
                            "python_file": driver_uri,
                            "parameters": [
                                "--probe-id",
                                probe_key,
                                "--runtime-surface",
                                runtime,
                                "--probe-output-table",
                                output_table,
                            ],
                        },
                    }
                ],
            }

    return {
        "status": "ready" if runtime_evidence else "probe_required" if job_submit_body else "blocked",
        "job_submit_body": job_submit_body,
        "runtime_evidence": runtime_evidence,
        "findings": findings,
        "next_action": (
            "Use runtime_evidence in skill:ml.training-backend-select."
            if runtime_evidence
            else "Run the probe job and pass its emitted row back as probe_result."
            if job_submit_body
            else "Bind a probe driver, probe id, and output table."
        ),
    }


def _runtime_evidence_from_result(
    probe_result: dict[str, Any] | None,
    *,
    runtime_surface: str,
) -> dict[str, Any] | None:
    if not isinstance(probe_result, dict):
        return None
    return {
        "runtime_surface": str(probe_result.get("runtime_surface") or runtime_surface),
        "databricks_automl_available": bool(probe_result.get("databricks_automl_available")),
        "spark_ml_allowed": bool(probe_result.get("spark_ml_allowed")),
        "mlflow_uc_registry_available": bool(probe_result.get("mlflow_uc_registry_available")),
        "mosaic_ai_available": bool(probe_result.get("mosaic_ai_available")),
        "substrate": _object_or_json(probe_result.get("substrate") or probe_result.get("substrate_json")),
        "probe_id": str(probe_result.get("probe_id") or ""),
        "errors": _object_or_json(probe_result.get("errors") or probe_result.get("errors_json")),
    }


def _valid_driver_uri(value: str) -> bool:
    return value.startswith(("dbfs:/", "/Workspace/", "/Volumes/"))


def _object_or_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return dict(parsed) if isinstance(parsed, dict) else {"raw": parsed}
    return {}


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


__all__ = ["SKILL", "run_ml_training_backend_probe"]
