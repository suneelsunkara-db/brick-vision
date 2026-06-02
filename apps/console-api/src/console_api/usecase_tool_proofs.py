"""Executable tool proof tracking for usecase skill families."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import databricks_sql
from .evaluation_events import emit_evaluation_event
from .skill_execution_service import execute_skill_for_usecase
from .skill_runtime_registry import resolve_family_runtime, skill_ids_for_family
from .usecase_records import get_usecase_record

TOOL_FAMILIES = ("SQL", "PySpark", "ML", "Migration", "Code Convert", "AI")
REQUIRED_PROOF_FAMILIES = {"SQL", "PySpark", "ML", "Migration"}


def list_usecase_tool_proofs(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Return the latest proof status for each executable tool family."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record
    latest = _load_latest_tool_proofs(usecase_id=usecase_id)
    proofs = [
        latest.get(family) or _unproven_tool_family(usecase_id, family)
        for family in TOOL_FAMILIES
    ]
    required_proofs = [item for item in proofs if item.get("family") in REQUIRED_PROOF_FAMILIES]
    return {
        "status": "ready" if all(_proof_ready(item) for item in required_proofs) else "incomplete",
        "usecase_id": usecase_id,
        "proofs": proofs,
        "next_action": _next_tool_proof_action(proofs),
    }


def run_usecase_tool_proof(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one real tool proof and persist the result."""

    normalized = family.strip()
    if normalized not in TOOL_FAMILIES:
        payload = {
            "status": "invalid_family",
            "usecase_id": usecase_id,
            "message": f"Unsupported tool proof family: {family}",
            "allowed_families": list(TOOL_FAMILIES),
        }
        _emit_tool_proof_event(
            user_id=user_id,
            usecase_id=usecase_id,
            family=normalized or family,
            proof=payload,
            reason_codes=["INVALID_TOOL_PROOF_FAMILY"],
        )
        return payload
    if normalized in {"Migration", "Code Convert"}:
        return _skill_execution_proof(
            user_id=user_id,
            usecase_id=usecase_id,
            family=normalized,
            execution_inputs=execution_inputs,
        )
    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record
    proof = (
        _skill_execution_proof(
            user_id=user_id,
            usecase_id=usecase_id,
            family=normalized,
            execution_inputs=execution_inputs,
        )
        if normalized in {"SQL", "PySpark", "ML", "Migration", "Code Convert"}
        else _runtime_registry_proof(
            user_id=user_id,
            usecase_id=usecase_id,
            family=normalized,
        )
    )
    if normalized != "Code Convert":
        _persist_tool_proof(proof)
        _emit_tool_proof_event(
            user_id=user_id,
            usecase_id=usecase_id,
            family=normalized,
            proof=proof,
            reason_codes=_reason_codes_for_proof(proof),
        )
    return proof


def _skill_execution_proof(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    execution = execute_skill_for_usecase(
        user_id=user_id,
        usecase_id=usecase_id,
        family=family,
        execution_inputs=execution_inputs,
    )
    passed = execution.get("status") in {
        "execution_proven",
        "training_execution_proven",
        "transpilation_proven",
        "transpilation_completed",
        "code_conversion_completed",
        "code_conversion_submitted",
    }
    return {
        "proof_id": proof_id_for(
            usecase_id=usecase_id,
            family=family,
            created_at_ms=now_ms,
        ),
        "usecase_id": usecase_id,
        "family": family,
        "status": (
            str(execution.get("status") or "execution_proven")
            if passed
            else str(execution.get("status") or "execution_failed")
        ),
        "skill_id": _family_skill_id(family),
        "result": execution,
        "created_by": user_id,
        "created_at_ms": now_ms,
        "next_action": (
            f"{family} skill execution is proven for this usecase."
            if passed
            else str(execution.get("message") or f"Fix {family} skill execution blockers.")
        ),
    }


def _runtime_registry_proof(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    runtime = resolve_family_runtime(family)
    return {
        "proof_id": proof_id_for(
            usecase_id=usecase_id,
            family=family,
            created_at_ms=now_ms,
        ),
        "usecase_id": usecase_id,
        "family": family,
        "status": str(runtime.get("status") or "blocked"),
        "skill_id": _family_skill_id(family),
        "result": {
            "executed": False,
            "proof_kind": "skill_runtime_registry",
            "message": (
                "This proof checks BrickVision skill contracts and runtime adapters. "
                "It does not bypass skills with direct Databricks job or notebook code."
            ),
            "runtime": runtime,
        },
        "created_by": user_id,
        "created_at_ms": now_ms,
        "next_action": str(runtime.get("next_action") or f"Resolve {family} runtime blockers."),
    }


def _ensure_tool_proof_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_tool_proofs")} (
          proof_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          family STRING NOT NULL,
          status STRING NOT NULL,
          skill_id STRING,
          result_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_tool_proofs')
        """
    )


def _persist_tool_proof(proof: dict[str, Any]) -> None:
    _ensure_tool_proof_table()
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_tool_proofs")} (
          proof_id,
          usecase_id,
          family,
          status,
          skill_id,
          result_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(proof.get("proof_id", "")))},
          {databricks_sql.sql_string_literal(str(proof.get("usecase_id", "")))},
          {databricks_sql.sql_string_literal(str(proof.get("family", "")))},
          {databricks_sql.sql_string_literal(str(proof.get("status", "")))},
          {databricks_sql.sql_string_literal(str(proof.get("skill_id", "")))},
          {databricks_sql.sql_string_literal(json.dumps(proof.get("result", {}), sort_keys=True))},
          {databricks_sql.sql_string_literal(str(proof.get("created_by", "")))},
          {int(proof.get("created_at_ms") or 0)}
        )
        """
    )


def _load_latest_tool_proofs(*, usecase_id: str) -> dict[str, dict[str, Any]]:
    try:
        _ensure_tool_proof_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT family, proof_id, status, skill_id, result_json, created_by, created_at_ms
            FROM (
              SELECT
                *,
                ROW_NUMBER() OVER (
                  PARTITION BY family
                  ORDER BY created_at_ms DESC
                ) AS rn
              FROM {databricks_sql.qualified_uc_name("workspace_usecase_tool_proofs")}
              WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            )
            WHERE rn = 1
            """
        )
    except Exception:
        return {}

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row[0])
        result = _decode_json(row[4])
        latest[family] = {
            "family": family,
            "proof_id": str(row[1]),
            "status": str(row[2]),
            "skill_id": str(row[3]) if row[3] is not None else "",
            "result": result if isinstance(result, dict) else {},
            "created_by": str(row[5]) if row[5] is not None else "",
            "created_at_ms": int(row[6]) if row[6] is not None else None,
            "next_action": _next_action_for_status(family, str(row[2])),
        }
    return latest


def _unproven_tool_family(usecase_id: str, family: str) -> dict[str, Any]:
    runtime = resolve_family_runtime(family)
    return {
        "usecase_id": usecase_id,
        "family": family,
        "status": str(runtime.get("status") or "not_run"),
        "skill_id": _family_skill_id(family),
        "result": {
            "executed": False,
            "proof_kind": "skill_runtime_registry",
            "runtime": runtime,
        },
        "next_action": str(runtime.get("next_action") or f"Run the {family} tool proof."),
    }


def _family_skill_id(family: str) -> str:
    skill_ids = skill_ids_for_family(family)
    return skill_ids[0] if skill_ids else ""


def _next_tool_proof_action(proofs: list[dict[str, Any]]) -> str:
    for proof in proofs:
        if proof.get("family") not in REQUIRED_PROOF_FAMILIES:
            continue
        if not _proof_ready(proof):
            return str(proof.get("next_action") or f"Run {proof['family']} proof.")
    return "SQL, PySpark, ML, and SQL transpilation proofs are available; optional PySpark Code Convert preflight can be run separately."


def _next_action_for_status(family: str, status: str) -> str:
    if status in {
        "execution_proven",
        "training_execution_proven",
        "transpilation_proven",
        "transpilation_completed",
        "code_conversion_completed",
        "code_conversion_submitted",
    }:
        return f"{family} skill execution is proven for this usecase."
    if status in {"execution_blocked", "code_conversion_blocked"}:
        return f"Bind {family} execution inputs before running the skill."
    if status == "runtime_ready":
        return f"{family} skill contracts and runtime adapters are available."
    if status == "failed":
        return f"Fix {family} runtime/configuration and rerun proof."
    if status in {"runtime_adapter_missing", "tool_adapter_missing", "skill_contract_invalid"}:
        return f"Resolve {family} skill runtime blockers."
    return f"Run the {family} tool proof."


def _proof_ready(proof: dict[str, Any]) -> bool:
    if proof.get("family") in {"SQL", "PySpark", "ML", "Migration", "Code Convert"}:
        return proof.get("status") in {
            "execution_proven",
            "training_execution_proven",
            "transpilation_proven",
            "transpilation_completed",
            "code_conversion_completed",
            "code_conversion_submitted",
        }
    return proof.get("status") == "runtime_ready"


def _emit_tool_proof_event(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
    proof: dict[str, Any],
    reason_codes: list[str],
) -> None:
    result = proof.get("result") if isinstance(proof.get("result"), dict) else {}
    emit_evaluation_event(
        event_kind="tool_proof",
        workflow="skill_execution",
        status=_event_status_for_proof(str(proof.get("status") or "")),
        subject_id=f"{usecase_id}:{family}",
        user_id=user_id,
        metrics={
            "executed": bool(result.get("executed")),
            "family": family,
            "proof_ready": _proof_ready(proof),
        },
        inputs={
            "usecase_id": usecase_id,
            "family": family,
            "skill_id": str(proof.get("skill_id") or ""),
        },
        outputs=proof,
        evidence=[],
        reason_codes=reason_codes,
    )


def _event_status_for_proof(status: str) -> str:
    if status in {
        "execution_proven",
        "training_execution_proven",
        "transpilation_proven",
        "transpilation_completed",
        "code_conversion_completed",
        "code_conversion_submitted",
        "runtime_ready",
    }:
        return "passed"
    if status in {"execution_blocked", "code_conversion_blocked", "blocked", "skill_contract_missing"}:
        return "blocked"
    return "failed"


def _reason_codes_for_proof(proof: dict[str, Any]) -> list[str]:
    status = str(proof.get("status") or "")
    if status in {
        "execution_proven",
        "training_execution_proven",
        "transpilation_proven",
        "transpilation_completed",
        "code_conversion_completed",
        "code_conversion_submitted",
        "runtime_ready",
    }:
        return []
    result = proof.get("result") if isinstance(proof.get("result"), dict) else {}
    if result.get("missing_inputs"):
        return ["TOOL_PROOF_MISSING_INPUTS"]
    if result.get("error_kind"):
        return [str(result["error_kind"])]
    return [status.upper() or "TOOL_PROOF_FAILED"]


def _decode_json(value: Any) -> Any:  # noqa: ANN401
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value


def proof_id_for(*, usecase_id: str, family: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {
            "usecase_id": usecase_id,
            "family": family,
            "created_at_ms": created_at_ms,
        },
        sort_keys=True,
    )
    return "uctp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
