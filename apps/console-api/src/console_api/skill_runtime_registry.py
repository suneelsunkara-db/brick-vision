"""Skill contract and runtime-adapter registry.

The skill catalog is the source of truth. This registry answers whether a
usecase can execute a skill through its declared contract, rather than through a
parallel proof implementation.
"""

from __future__ import annotations

from typing import Any

from .skill_contracts import (
    TOOL_RUNTIME_MODULES,
    import_skill_module,
    list_execution_families,
    load_skill_contract,
    runtime_target_exists,
    skill_ids_for_family,
    skill_import_error,
)


def resolve_family_runtime(family: str) -> dict[str, Any]:
    """Resolve one tool family against skill contracts and runtime adapters."""

    skill_ids = skill_ids_for_family(family)
    skills = [_resolve_skill(skill_id) for skill_id in skill_ids]
    blocking = [
        finding
        for skill in skills
        for finding in skill["findings"]
        if finding["severity"] == "blocking"
    ]
    status = "runtime_ready" if skills and not blocking else _status_from_findings(blocking)
    return {
        "family": family,
        "status": status,
        "skills": skills,
        "findings": [finding for skill in skills for finding in skill["findings"]],
        "next_action": _next_action(family=family, status=status, findings=blocking),
    }


def resolve_skill_runtime(skill_id: str) -> dict[str, Any]:
    """Resolve one skill against its contract and declared runtime adapters."""

    return _resolve_skill(skill_id)


def list_family_runtimes() -> list[dict[str, Any]]:
    """Return runtime readiness for all execution families used by usecases."""

    return [resolve_family_runtime(family) for family in list_execution_families()]


def _resolve_skill(skill_id: str) -> dict[str, Any]:
    findings: list[dict[str, str]] = []

    contract = load_skill_contract(skill_id)
    if contract is None:
        findings.append(_finding("blocking", "SKILL_CONTRACT_MISSING", f"{skill_id} has no SKILL.yaml."))
        return _skill_result(skill_id, "", (), findings, runner_name="")

    tools = tuple(sorted(set(contract.tools)))
    if not contract.skill_py.exists():
        findings.append(_finding("blocking", "SKILL_MODULE_MISSING", f"{skill_id} has no skill.py."))
        return _skill_result(skill_id, str(contract.skill_dir), tools, findings, runner_name=contract.runner_name)

    import_error = skill_import_error(contract)
    if import_error:
        findings.append(
            _finding(
                "blocking",
                "SKILL_MODULE_IMPORT_FAILED",
                f"{skill_id} skill.py is not importable: {import_error}",
            )
        )

    for tool in tools:
        modules = TOOL_RUNTIME_MODULES.get(tool)
        if not modules:
            findings.append(
                _finding(
                    "blocking",
                    "TOOL_ADAPTER_UNREGISTERED",
                    f"{tool} is declared by {skill_id} but is not in the tool adapter registry.",
                )
            )
            continue
        for module in modules:
            if not runtime_target_exists(module):
                findings.append(
                    _finding(
                        "blocking",
                        "TOOL_RUNTIME_MODULE_MISSING",
                        f"{tool} requires {module}, but that module is not importable.",
                    )
                )

    try:
        skill_module = import_skill_module(contract, prefix="_brickvision_runner_probe")
        if not callable(getattr(skill_module, contract.runner_name, None)):
            findings.append(
                _finding(
                    "blocking",
                    "SKILL_RUNNER_MISSING",
                    f"{skill_id} does not expose {contract.runner_name}.",
                )
            )
    except Exception as exc:
        findings.append(
            _finding(
                "blocking",
                "SKILL_MODULE_IMPORT_FAILED",
                f"{skill_id} skill.py is not importable: {type(exc).__name__}: {exc}",
            )
        )

    if not findings:
        findings.append(_finding("info", "SKILL_RUNTIME_READY", f"{skill_id} contract and runtime adapters resolved."))
    return _skill_result(
        skill_id,
        str(contract.skill_dir),
        tools,
        findings,
        runner_name=contract.runner_name,
    )


def _skill_result(
    skill_id: str,
    skill_dir: str,
    tools: tuple[str, ...],
    findings: list[dict[str, str]],
    runner_name: str,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "skill_dir": skill_dir,
        "declared_tools": list(tools),
        "runner_name": runner_name,
        "status": (
            "runtime_ready"
            if not any(item["severity"] == "blocking" for item in findings)
            else _status_from_findings(findings)
        ),
        "findings": findings,
    }


def _status_from_findings(findings: list[dict[str, str]]) -> str:
    codes = {item["code"] for item in findings}
    if "SKILL_CONTRACT_MISSING" in codes or "SKILL_CONTRACT_ID_MISMATCH" in codes:
        return "skill_contract_invalid"
    if (
        "SKILL_MODULE_MISSING" in codes
        or "SKILL_MODULE_IMPORT_FAILED" in codes
        or "SKILL_RUNNER_MISSING" in codes
    ):
        return "runtime_adapter_missing"
    if "TOOL_ADAPTER_UNREGISTERED" in codes or "TOOL_RUNTIME_MODULE_MISSING" in codes:
        return "tool_adapter_missing"
    return "blocked"


def _next_action(
    *,
    family: str,
    status: str,
    findings: list[dict[str, str]],
) -> str:
    if status == "runtime_ready":
        return f"{family} skill contracts and runtime adapters are available; execution proof can be implemented through the skill runner."
    if not findings:
        return f"No {family} skill contract is registered for usecase execution."
    if findings:
        return findings[0]["message"]
    return f"Resolve {family} skill runtime blockers."

def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}
