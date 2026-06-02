"""Mechanical Layer-0 skill: ``skill:databricks.statement-execute``."""

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
    id="skill:databricks.statement-execute",
    version="0.1.0",
    dag=DAG(name="databricks.statement-execute"),
    constitutional=(
        "api.operations.must.cite.capability-graph",
        "mutating.api.requests.must.have-bound-statement",
        "sql.execution.must.use-bound-warehouse",
    ),
)


def run_databricks_statement_execute(
    *,
    capability_evidence: list[dict[str, Any]],
    statement: str | None = None,
    warehouse_id: str | None = None,
    statement_operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    capability_refs = _capability_refs(capability_evidence)
    if not capability_refs:
        findings.append(
            _finding(
                "blocking",
                "STATEMENT_CAPABILITY_EVIDENCE_REQUIRED",
                "Statement Execution binding needs indexed SQL Statement API capability evidence.",
            )
        )
    if has_contract_only_capability_evidence(capability_evidence):
        findings.append(
            _finding(
                "blocking",
                "HAND_AUTHORED_CAPABILITY_EVIDENCE_REJECTED",
                "Hand-authored skill contracts are not source-grounded Statement Execution evidence.",
            )
        )

    sql_text = str(statement or "").strip()
    if not sql_text:
        findings.append(
            _finding(
                "blocking",
                "SQL_STATEMENT_REQUIRED",
                "Statement Execution needs a concrete SQL statement before execution.",
            )
        )
    warehouse = str(warehouse_id or "").strip()
    if not warehouse:
        findings.append(
            _finding(
                "blocking",
                "WAREHOUSE_ID_REQUIRED",
                "Statement Execution needs a bound Databricks SQL warehouse_id.",
            )
        )

    raw_operation = statement_operation or {
        "operation_id": _statement_ref(capability_refs) or "openapi:2.0:StatementExecutionExecuteStatement",
        "method": "POST",
        "path": "/api/2.0/sql/statements",
        "capability_refs": list(capability_refs),
    }
    operation = _operation(raw_operation, statement=sql_text, warehouse_id=warehouse)
    if operation is None:
        findings.append(
            _finding(
                "blocking",
                "STATEMENT_OPERATION_INVALID",
                "Statement operation must be POST /api/.../sql/statements and cite capability refs.",
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
            "Attach this Statement Execution operation to the downstream API execution plan."
            if operation
            else "Bind Statement Execution capability evidence, warehouse_id, and SQL text."
        ),
    }


def _operation(raw: dict[str, Any], *, statement: str, warehouse_id: str) -> dict[str, Any] | None:
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
        or "/sql/statements" not in path
        or not refs
        or not statement
        or not warehouse_id
    ):
        return None
    return {
        "operation_id": operation_id,
        "method": method,
        "path": path,
        "body": {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "0s",
            "disposition": "INLINE",
        },
        "capability_refs": list(refs),
        "wait": {"kind": "sql_statement_succeeded", "timeout_sec": 1800, "poll_sec": 5},
    }


def _capability_refs(items: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(source_grounded_capability_refs(item for item in items if isinstance(item, dict)))


def _statement_ref(refs: tuple[str, ...]) -> str | None:
    return next((ref for ref in refs if "statement" in ref.lower() or "sql" in ref.lower()), None)


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_databricks_statement_execute"]
