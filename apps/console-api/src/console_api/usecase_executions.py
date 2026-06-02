"""In-app execution monitor for long-running usecase skill proofs."""

from __future__ import annotations

import concurrent.futures
import threading
import time
import uuid
from typing import Any

from .usecase_tool_proofs import TOOL_FAMILIES, run_usecase_tool_proof

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="bv-execution")
_LOCK = threading.Lock()
_RUNS: dict[str, dict[str, Any]] = {}


def start_usecase_execution(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start one real proof execution and return immediately for UI polling."""

    normalized = family.strip()
    if normalized not in TOOL_FAMILIES:
        return {
            "status": "invalid_family",
            "usecase_id": usecase_id,
            "message": f"Unsupported execution family: {family}",
            "allowed_families": list(TOOL_FAMILIES),
        }
    with _LOCK:
        for existing in _RUNS.values():
            if (
                str(existing.get("usecase_id")) == usecase_id
                and str(existing.get("family")) == normalized
                and str(existing.get("status")) in {"queued", "running"}
            ):
                return dict(existing)
    execution_id = f"ucex_{uuid.uuid4().hex[:24]}"
    run = {
        "execution_id": execution_id,
        "usecase_id": usecase_id,
        "family": normalized,
        "status": "queued",
        "created_by": user_id,
        "created_at_ms": _now_ms(),
        "updated_at_ms": _now_ms(),
        "steps": [
            _step("queued", "queued", "Execution accepted by the local sidecar."),
            _step("skill_chain", "pending", "Run the persisted Skill Builder proof chain."),
            _step("proof_persist", "pending", "Persist the final proof row for the usecase."),
        ],
        "result": None,
        "error": None,
        "durable": False,
        "execution_inputs": dict(execution_inputs or {}),
        "next_action": "Execution is queued.",
    }
    with _LOCK:
        _RUNS[execution_id] = run
    _EXECUTOR.submit(
        _run_execution,
        execution_id,
        user_id,
        usecase_id,
        normalized,
        dict(execution_inputs or {}),
    )
    return dict(run)


def list_usecase_executions(*, usecase_id: str) -> dict[str, Any]:
    """Return active/recent in-process executions for a usecase."""

    with _LOCK:
        runs = [
            dict(run)
            for run in _RUNS.values()
            if str(run.get("usecase_id")) == usecase_id
        ]
    runs.sort(key=lambda item: int(item.get("created_at_ms") or 0), reverse=True)
    return {
        "status": "ready",
        "usecase_id": usecase_id,
        "executions": runs[:20],
        "next_action": (
            "Open an execution or start a new family proof."
            if runs
            else "No active execution monitor runs yet."
        ),
    }


def get_usecase_execution(*, usecase_id: str, execution_id: str) -> dict[str, Any]:
    """Return one in-process execution monitor run."""

    with _LOCK:
        run = _RUNS.get(execution_id)
        if run is None or str(run.get("usecase_id")) != usecase_id:
            return {
                "status": "not_found",
                "usecase_id": usecase_id,
                "execution_id": execution_id,
                "message": "Execution monitor run was not found in this sidecar session.",
            }
        return dict(run)


def _run_execution(
    execution_id: str,
    user_id: str,
    usecase_id: str,
    family: str,
    execution_inputs: dict[str, Any],
) -> None:
    _update(
        execution_id,
        status="running",
        next_action=f"Running {family} skill proof.",
        steps={
            "queued": "completed",
            "skill_chain": "running",
        },
    )
    try:
        proof = run_usecase_tool_proof(
            user_id=user_id,
            usecase_id=usecase_id,
            family=family,
            execution_inputs=execution_inputs,
        )
    except Exception as exc:  # pragma: no cover - defensive for local sidecar worker
        _update(
            execution_id,
            status="failed",
            error={"error_kind": type(exc).__name__, "message": str(exc)},
            next_action=f"{family} execution failed before proof persistence.",
            steps={
                "skill_chain": "failed",
                "proof_persist": "skipped",
            },
        )
        return

    proof_status = str(proof.get("status") or "")
    completed = proof_status in {
        "execution_proven",
        "training_execution_proven",
        "transpilation_proven",
        "transpilation_completed",
        "code_conversion_completed",
        "code_conversion_submitted",
    }
    _update(
        execution_id,
        status="completed" if completed else "blocked",
        result=proof,
        next_action=str(proof.get("next_action") or f"{family} execution finished."),
        steps={
            "skill_chain": "completed" if completed else "blocked",
            "proof_persist": "completed",
        },
    )


def _update(
    execution_id: str,
    *,
    status: str,
    next_action: str | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
    steps: dict[str, str] | None = None,
) -> None:
    with _LOCK:
        run = _RUNS.get(execution_id)
        if run is None:
            return
        run["status"] = status
        run["updated_at_ms"] = _now_ms()
        if next_action is not None:
            run["next_action"] = next_action
        if result is not None:
            run["result"] = result
        if error is not None:
            run["error"] = error
        if steps:
            for step in run.get("steps", []):
                step_id = str(step.get("step_id") or "")
                if step_id in steps:
                    step["status"] = steps[step_id]
                    step["updated_at_ms"] = _now_ms()


def _step(step_id: str, status: str, label: str) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "status": status,
        "label": label,
        "updated_at_ms": _now_ms(),
    }


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "get_usecase_execution",
    "list_usecase_executions",
    "start_usecase_execution",
]
