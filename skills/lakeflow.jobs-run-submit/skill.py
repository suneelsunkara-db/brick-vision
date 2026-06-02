"""Mechanical Layer-0 skill: ``skill:lakeflow.jobs-run-submit``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.capability_evidence import (
    has_contract_only_capability_evidence,
    is_capability_ref,
    source_grounded_capability_refs,
)
from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:lakeflow.jobs-run-submit",
    version="0.1.0",
    dag=DAG(name="lakeflow.jobs-run-submit"),
    constitutional=(
        "api.operations.must.cite.capability-graph",
        "mutating.api.requests.must.have.bound-body",
    ),
)


def run_lakeflow_jobs_run_submit(
    *,
    capability_evidence: list[dict[str, Any]],
    job_submit_body: dict[str, Any] | None = None,
    job_submit_operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    capability_refs = _capability_refs(capability_evidence)
    if not capability_refs:
        findings.append(
            _finding(
                "blocking",
                "JOBS_CAPABILITY_EVIDENCE_REQUIRED",
                "Jobs submit binding needs indexed Jobs API capability evidence.",
            )
        )
    if has_contract_only_capability_evidence(capability_evidence):
        findings.append(
            _finding(
                "blocking",
                "HAND_AUTHORED_CAPABILITY_EVIDENCE_REJECTED",
                "Hand-authored skill contracts are not source-grounded Jobs API evidence.",
            )
        )

    body = dict(job_submit_body or {})
    if not body:
        findings.append(
            _finding(
                "blocking",
                "JOB_SUBMIT_BODY_REQUIRED",
                "Jobs runs/submit needs a concrete request body before execution.",
            )
        )

    raw_operation = job_submit_operation or {
        "operation_id": _jobs_ref(capability_refs) or "openapi:2.1:JobsRunsSubmit",
        "method": "POST",
        "path": "/api/2.1/jobs/runs/submit",
        "capability_refs": list(capability_refs),
    }
    operation = _operation(raw_operation, body=body)
    if operation is None:
        findings.append(
            _finding(
                "blocking",
                "JOBS_SUBMIT_OPERATION_INVALID",
                "Jobs submit operation must be POST /api/.../jobs/runs/submit and cite capability refs.",
            )
        )

    if _blocking(findings):
        operation = None

    return {
        "status": "ready" if operation else "blocked",
        "api_operation": operation,
        "capability_refs": list(capability_refs),
        "findings": findings,
        "next_action": (
            "Attach this Jobs submit operation to the downstream API execution plan."
            if operation
            else "Bind Jobs capability evidence and a concrete runs/submit request body."
        ),
    }


def _operation(raw: dict[str, Any], *, body: dict[str, Any]) -> dict[str, Any] | None:
    method = str(raw.get("method") or raw.get("http_method") or "").upper().strip()
    path = str(raw.get("path") or raw.get("api_path") or "").strip()
    refs = _capability_refs([raw])
    operation_id = str(raw.get("operation_id") or raw.get("entity_id") or "").strip()
    if not refs and is_capability_ref(operation_id):
        refs = (operation_id,)
    if (
        not operation_id
        or method != "POST"
        or not path.startswith("/api/")
        or "/jobs/runs/submit" not in path
        or not refs
        or not body
    ):
        return None
    return {
        "operation_id": operation_id,
        "method": method,
        "path": path,
        "body": body,
        "capability_refs": list(refs),
        "wait": {"kind": "jobs_run_terminated", "timeout_sec": 3600, "poll_sec": 15},
    }


def _capability_refs(items: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(source_grounded_capability_refs(item for item in items if isinstance(item, dict)))


def _jobs_ref(refs: tuple[str, ...]) -> str | None:
    return next((ref for ref in refs if "JobsRunsSubmit" in ref or "jobs-runs-submit" in ref), None)


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_lakeflow_jobs_run_submit"]
