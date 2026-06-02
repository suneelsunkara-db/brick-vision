"""Skill Builder read model backed by real SKILL.yaml contracts."""

from __future__ import annotations

from typing import Any

from .skill_contracts import EXECUTION_ADAPTER_GAPS, TOOL_RUNTIME_MODULES, list_skill_contracts
from .skill_runtime_registry import list_family_runtimes, resolve_skill_runtime


def list_skill_builder_contracts(*, user_id: str) -> dict[str, Any]:
    """Return Skill Builder inventory from checked-in skill contracts."""

    _ = user_id
    contracts = [
        _contract_payload(contract)
        for contract in list_skill_contracts()
    ]
    contracts = [contract for contract in contracts if contract["skill_id"]]
    contracts.sort(key=lambda item: (item["category"], item["title"], item["skill_id"]))
    summary = _summary(contracts)
    return {
        "status": "ready",
        "summary": summary,
        "skills": contracts,
        "execution_families": [_family_readiness(item) for item in list_family_runtimes()],
        "skill_gaps": _skill_gaps(contracts),
        "next_action": _next_action(summary),
    }


def _contract_payload(contract: Any) -> dict[str, Any]:
    runtime = resolve_skill_runtime(contract.skill_id)
    payload = contract.to_public_dict()
    payload["readiness"] = _readiness(runtime)
    payload["runtime_check"] = runtime
    return payload


def _summary(contracts: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [item for item in contracts if item["readiness"]["status"] == "ready"]
    needs_work = [item for item in contracts if item["readiness"]["status"] != "ready"]
    by_category: dict[str, int] = {}
    for item in contracts:
        category = str(item["category"])
        by_category[category] = by_category.get(category, 0) + 1
    return {
        "skill_count": len(contracts),
        "ready_count": len(ready),
        "needs_work_count": len(needs_work),
        "by_category": by_category,
    }


def _next_action(summary: dict[str, Any]) -> str:
    if int(summary["needs_work_count"]) > 0:
        return "Review skills that need work before usecases depend on them."
    return "Core skill contracts are ready for usecase planning."


def _readiness(runtime: dict[str, Any]) -> dict[str, Any]:
    status = str(runtime.get("status") or "unknown")
    findings = list(runtime.get("findings") or [])
    blocking = [
        finding for finding in findings
        if isinstance(finding, dict) and finding.get("severity") == "blocking"
    ]
    return {
        "status": "ready" if status == "runtime_ready" else "needs_work",
        "label": "Ready to use" if status == "runtime_ready" else "Needs work",
        "message": (
            "Skill contract, Python module, and declared tool adapters are available."
            if status == "runtime_ready"
            else str(runtime.get("next_action") or "Resolve skill contract or tool blockers.")
        ),
        "blocking_count": len(blocking),
    }


def _family_readiness(runtime: dict[str, Any]) -> dict[str, Any]:
    family = str(runtime.get("family") or "")
    status = str(runtime.get("status") or "blocked")
    skills = list(runtime.get("skills") or [])
    if family in EXECUTION_ADAPTER_GAPS and status == "runtime_ready":
        return {
            "family": family,
            "status": "needs_work",
            "label": "Needs execution adapter",
            "skill_ids": [str(skill.get("skill_id") or "") for skill in skills if skill.get("skill_id")],
            "message": EXECUTION_ADAPTER_GAPS[family],
        }
    return {
        "family": family,
        "status": "ready" if status == "runtime_ready" else "needs_work",
        "label": "Ready to execute" if status == "runtime_ready" else "Needs work",
        "skill_ids": [str(skill.get("skill_id") or "") for skill in skills if skill.get("skill_id")],
        "message": str(runtime.get("next_action") or "Resolve skill readiness blockers."),
    }


def _skill_gaps(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    skill_ids = {str(item.get("skill_id") or "") for item in contracts}
    gaps: list[dict[str, Any]] = []
    if "skill:migration.lakebridge-support-matrix" in skill_ids:
        if "skill:migration.lakebridge-assess" not in skill_ids:
            gaps.append(
                {
                    "gap_id": "gap:migration.lakebridge-assess",
                    "title": "Lakebridge Assessment skill",
                    "missing_skill_id": "skill:migration.lakebridge-assess",
                    "status": "recommended",
                    "priority": "high",
                    "evidence": "Migration Assessment has capability-graph coverage but no executable BrickVision assessment skill.",
                    "why_build": "Assessment should precede SQL Transpile and Code Convert so migration starts from source inventory and complexity evidence.",
                    "recommended_first_step": "Use skill:migration.lakebridge-support-matrix to decide which source systems can honestly be assessed.",
                }
            )
        gaps.append(
            {
                "gap_id": "gap:migration.lakebridge-reconcile",
                "title": "Lakebridge Reconcile skill",
                "missing_skill_id": "skill:migration.lakebridge-reconcile",
                "status": "recommended",
                "priority": "high",
                "evidence": "Migration Validation/Reconcile has capability-graph coverage and Lakebridge declares reconcile source types, but BrickVision has no reconcile workflow skill.",
                "why_build": "Converted SQL/code needs validation or source-target reconciliation evidence before it can be called proven.",
                "recommended_first_step": "Build a read-only reconcile config readiness skill before running data comparisons.",
            }
        )
    else:
        gaps.append(
            {
                "gap_id": "gap:migration.support-matrix",
                "title": "Lakebridge Support Matrix",
                "missing_skill_id": "skill:migration.lakebridge-support-matrix",
                "status": "build_first",
                "priority": "critical",
                "evidence": "Migration workflows need source-system support evidence before building assessment/reconcile skills.",
                "why_build": "Prevents BrickVision from claiming unsupported source-system coverage.",
                "recommended_first_step": "Inspect installed Lakebridge/Switch package resources.",
            }
        )
    if "tool:uc.list_volumes" not in TOOL_RUNTIME_MODULES:
        gaps.append(
            {
                "gap_id": "gap:uc.list-volumes",
                "title": "UC Volume list/binding tool adapter",
                "missing_skill_id": "tool:uc.list_volumes",
                "status": "blocking_existing_skill",
                "priority": "high",
                "evidence": "skill:migration.lakebridge-code-convert declares tool:uc.list_volumes and is marked needs work until the adapter is registered.",
                "why_build": "Code Convert and migration artifact staging depend on governed UC Volume paths.",
                "recommended_first_step": "Register a mechanical tool adapter that validates/list volumes through the Databricks SDK or CLI.",
            }
        )
    gaps.append(
        {
            "gap_id": "gap:ai.agent-evaluation-plan",
            "title": "AI agent evaluation planning skill",
            "missing_skill_id": "skill:ai.agent-evaluation-plan",
            "status": "recommended",
            "priority": "medium",
            "evidence": "Usecase candidates include AI Advisory Agent Evaluation, but the AI execution family has no registered skill contract.",
            "why_build": "BrickVision has KG/RAG surfaces but no skill that turns advisory session evidence into an evaluation plan.",
            "recommended_first_step": "Create a planning-only skill over advisory_sessions, advisory_messages, decisions, artifacts, and feedback.",
        }
    )
    gaps.append(
        {
            "gap_id": "gap:deploy.target-bind",
            "title": "Deployment target binding skill",
            "missing_skill_id": "skill:deploy.target-bind",
            "status": "recommended",
            "priority": "medium",
            "evidence": "Usecase candidates repeatedly show Deploy as needs_target, but there is no Deploy family.",
            "why_build": "Usecases need a clear target choice: table, job, dashboard, model endpoint, app, or monitoring destination.",
            "recommended_first_step": "Add a planning skill that binds artifact type to target and required permissions.",
        }
    )
    return gaps
