"""Mechanical Layer-0 skill: ``skill:delta.pyspark-task-plan``."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:delta.pyspark-task-plan",
    version="0.1.0",
    dag=DAG(name="delta.pyspark-task-plan"),
    constitutional=(
        "pyspark.driver.must.be-real-artifact-ref",
        "jobs.request.body.must.be-bound-before-submit",
        "runtime.adapters.must.not-embed-transform-driver-code",
    ),
)


def run_delta_pyspark_task_plan(
    *,
    transform_id: str,
    transform_code: str,
    input_uris: list[str],
    output_uri: str,
    expected_output_schema: dict[str, Any],
    pyspark_driver_uri: str | None = None,
    job_run_name: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    driver_uri = str(pyspark_driver_uri or "").strip()
    if not _valid_driver_uri(driver_uri):
        findings.append(
            _finding(
                "blocking",
                "PYSPARK_DRIVER_URI_REQUIRED",
                "Bind an existing Workspace, Volumes, or DBFS PySpark transform driver artifact URI.",
            )
        )

    transform_key = str(transform_id or "").strip()
    code = str(transform_code or "").strip()
    inputs = [str(item).strip() for item in input_uris if str(item).strip()]
    output = str(output_uri or "").strip()
    expected_schema = {str(key): str(value) for key, value in (expected_output_schema or {}).items()}
    missing = [
        name
        for name, value in {
            "transform_id": transform_key,
            "transform_code": code,
            "input_uris": inputs,
            "output_uri": output,
            "expected_output_schema": expected_schema,
        }.items()
        if not value
    ]
    if missing:
        findings.append(
            _finding(
                "blocking",
                "PYSPARK_TASK_INPUTS_REQUIRED",
                f"Missing PySpark task inputs: {', '.join(sorted(missing))}.",
            )
        )

    job_submit_body = None
    if not _blocking(findings):
        payload = _encoded_payload(
            {
                "transform_id": transform_key,
                "transform_code": code,
                "input_uris": inputs,
                "output_uri": output,
                "expected_output_schema": expected_schema,
            }
        )
        job_submit_body = {
            "run_name": job_run_name or f"brickvision-pyspark-{_slug(transform_key)}",
            "timeout_seconds": int(timeout_seconds or 1800),
            "tasks": [
                {
                    "task_key": "run_pyspark_transform",
                    "spark_python_task": {
                        "python_file": driver_uri,
                        "parameters": [payload],
                    },
                    "environment_key": "default",
                }
            ],
            "environments": [
                {
                    "environment_key": "default",
                    "spec": {
                        "client": "2",
                        "dependencies": [],
                    },
                }
            ],
        }

    return {
        "status": "ready" if job_submit_body else "blocked",
        "job_submit_body": job_submit_body,
        "findings": findings,
        "next_action": (
            "Pass job_submit_body to skill:lakeflow.jobs-run-submit."
            if job_submit_body
            else "Bind a real PySpark driver artifact and task inputs."
        ),
    }


def _encoded_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _valid_driver_uri(value: str) -> bool:
    return value.startswith(("dbfs:/", "/Workspace/", "/Volumes/"))


def _slug(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    cleaned = value.replace("`", "").replace(".", "-").replace("_", "-")[-48:] or "transform"
    return f"{cleaned}-{digest}"


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_delta_pyspark_task_plan"]
