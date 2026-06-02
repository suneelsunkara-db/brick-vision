"""Mechanical Layer-0 skill: ``skill:ml.api-plan-bind``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.capability_evidence import (
    has_contract_only_capability_evidence,
    source_grounded_capability_refs,
)
from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.api-plan-bind",
    version="0.1.0",
    dag=DAG(name="ml.api-plan-bind"),
    constitutional=(
        "training.must.not.run.before.strategy-approval",
        "api.operations.must.cite.capability-graph",
        "mutating.api.requests.must.have.bound-body",
    ),
)


def run_ml_api_plan_bind(
    *,
    strategy_plan: dict[str, Any],
    capability_evidence: list[dict[str, Any]],
    job_submit_body: dict[str, Any] | None = None,
    job_submit_plan: dict[str, Any] | None = None,
    audit_readback_operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    strategy = dict(strategy_plan)
    if strategy.get("status") != "ready_for_approval":
        findings.append(_finding("blocking", "STRATEGY_NOT_APPROVED", "Strategy must be ready_for_approval."))

    capability_refs = _capability_refs(capability_evidence)
    if not capability_refs:
        findings.append(
            _finding(
                "blocking",
                "CAPABILITY_EVIDENCE_REQUIRED",
                "API plan binding needs indexed SDK/OpenAPI/docs capability evidence.",
            )
        )
    if has_contract_only_capability_evidence(capability_evidence):
        findings.append(
            _finding(
                "blocking",
                "HAND_AUTHORED_CAPABILITY_EVIDENCE_REJECTED",
                "Hand-authored skill contracts are not source-grounded API binding evidence.",
            )
        )

    job_operation = _job_operation_from_plan(job_submit_plan)
    if job_operation is None and job_submit_body:
        findings.append(
            _finding(
                "warning",
                "DIRECT_JOB_SUBMIT_BODY_DEPRECATED",
                "Use skill:lakeflow.jobs-run-submit to bind Jobs submit operations.",
            )
        )
    if job_operation is None:
        findings.append(
            _finding(
                "blocking",
                "JOBS_SUBMIT_PLAN_REQUIRED",
                "ML API plan binding requires output from skill:lakeflow.jobs-run-submit.",
            )
        )

    audit_operation = _operation(audit_readback_operation, body=None, require_body=False)
    if audit_operation is None:
        findings.append(
            _finding(
                "blocking",
                "AUDIT_READBACK_OPERATION_REQUIRED",
                "API plan binding needs a grounded operation to read back the ModelTrainingRun audit row.",
            )
        )

    api_execution_plan = None
    if not _blocking(findings) and job_operation and audit_operation:
        api_execution_plan = {
            "plan_id": f"{strategy.get('problem_type') or 'ml'}-databricks-api-plan",
            "capability_refs": list(capability_refs),
            "operations": [job_operation],
            "audit_readback": {"operation": audit_operation},
        }
        strategy["api_execution_plan"] = api_execution_plan

    return {
        "status": "ready" if api_execution_plan else "blocked",
        "strategy_plan": strategy,
        "api_execution_plan": api_execution_plan,
        "findings": findings,
        "next_action": (
            "Run skill:ml.train-evaluate-register with this bound API plan."
            if api_execution_plan
            else "Bind a concrete Jobs submit body and audit readback operation before training."
        ),
    }


def _operation(
    raw: dict[str, Any] | None,
    *,
    body: dict[str, Any] | None,
    require_body: bool,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    operation_body = body if body is not None else raw.get("body")
    operation = {
        "operation_id": str(raw.get("operation_id") or raw.get("entity_id") or "").strip(),
        "method": str(raw.get("method") or raw.get("http_method") or "").upper().strip(),
        "path": str(raw.get("path") or raw.get("api_path") or "").strip(),
        "body": dict(operation_body or {}),
        "capability_refs": list(_capability_refs([raw])),
        "wait": dict(raw.get("wait") or {}),
    }
    if (
        not operation["operation_id"]
        or operation["method"] not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        or not operation["path"].startswith("/api/")
        or not operation["capability_refs"]
    ):
        return None
    if require_body and not operation["body"]:
        return None
    return operation


def _job_operation_from_plan(job_submit_plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job_submit_plan, dict) or job_submit_plan.get("status") != "ready":
        return None
    operation = job_submit_plan.get("api_operation")
    if not isinstance(operation, dict):
        return None
    return _operation(operation, body=None, require_body=True)


def _capability_refs(items: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(source_grounded_capability_refs(item for item in items if isinstance(item, dict)))


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_ml_api_plan_bind"]
