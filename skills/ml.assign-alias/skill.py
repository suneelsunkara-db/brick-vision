"""Mechanical Layer-0 skill: ``skill:ml.assign-alias`` (N150).

Pure orchestrator — no LLM call. Validates invariants, then
delegates to ``brickvision_runtime.ml.alias_assigner.assign_alias``.
"""

from __future__ import annotations

# bv:templated:start id=imports
import uuid
from collections.abc import Callable
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.ml.alias_assigner import (
    AliasAssignment,
    SetAliasFn,
    assign_alias,
)
from brickvision_runtime.orchestration import DAG
# bv:templated:end id=imports


HitlApprovalCheck = Callable[[str, str, int], bool]
"""(model_full_name, alias, target_version) -> True if HITL approval recorded."""

ProductionAliasLookup = Callable[[str], bool]
"""(alias) -> True iff alias is in <bv>.policy.production_aliases."""

ValFloorPassedLookup = Callable[[str, int], bool]
"""(model_full_name, target_version) -> True iff ModelTrainingRun audit row carries val_floor_passed=True."""


# bv:templated:start id=skill
SKILL = Skill.mechanical(
    id="skill:ml.assign-alias",
    version="0.1.0",
    dag=DAG(name="ml.assign-alias"),
    constitutional=(
        "alias.assignment.requires.val.floor.passed",
        "alias.assignment.requires.hitl.for.production.aliases",
    ),
)
# bv:templated:end id=skill


# bv:templated:start id=runner
def run_ml_assign_alias(
    *,
    model_full_name: str,
    alias: str,
    target_version: int,
    val_floor_lookup: ValFloorPassedLookup,
    production_alias_lookup: ProductionAliasLookup,
    hitl_check: HitlApprovalCheck,
    set_alias: SetAliasFn,
    skill_id: str = "skill:ml.assign-alias",
    audit_id: str | None = None,
) -> dict[str, Any]:
    audit_id = audit_id or str(uuid.uuid4())
    val_floor_passed = val_floor_lookup(model_full_name, target_version)
    is_production = production_alias_lookup(alias)
    hitl_approved = hitl_check(model_full_name, alias, target_version)

    assignment, questions = assign_alias(
        model_full_name=model_full_name,
        alias=alias,
        target_version=target_version,
        val_floor_passed=val_floor_passed,
        is_production_alias=is_production,
        hitl_approved=hitl_approved,
        set_alias=set_alias,
        skill_id=skill_id,
        audit_id=audit_id,
    )
    return {
        "alias_assignment": assignment,
        "questions": list(questions),
        "is_production_alias": is_production,
    }


__all__ = [
    "AliasAssignment",
    "HitlApprovalCheck",
    "ProductionAliasLookup",
    "SKILL",
    "ValFloorPassedLookup",
    "run_ml_assign_alias",
]
# bv:templated:end id=runner
