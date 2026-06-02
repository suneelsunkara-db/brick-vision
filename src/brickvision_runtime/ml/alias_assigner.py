"""UC model alias assignment runtime contract."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from brickvision_runtime.failures import Question, ReasonCode, question_from_failure


@dataclasses.dataclass(frozen=True)
class AliasAssignment:
    audit_id: str
    model_full_name: str
    alias: str
    target_version: int
    skill_id: str
    val_floor_passed: bool
    hitl_approved: bool
    assigned: bool
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


SetAliasFn = Callable[[str, str, int], dict[str, Any]]


def assign_alias(
    *,
    model_full_name: str,
    alias: str,
    target_version: int,
    val_floor_passed: bool,
    is_production_alias: bool,
    hitl_approved: bool,
    set_alias: SetAliasFn,
    skill_id: str,
    audit_id: str,
) -> tuple[AliasAssignment, tuple[Question, ...]]:
    questions: list[Question] = []
    if not val_floor_passed:
        questions.append(
            _question(
                subject=model_full_name,
                details={"alias": alias, "target_version": target_version},
                action="Assign an alias only to a model version with val_floor_passed=true.",
            )
        )
    if is_production_alias and not hitl_approved:
        questions.append(
            _question(
                subject=model_full_name,
                details={"alias": alias, "target_version": target_version},
                action="Record HITL approval before assigning a production alias.",
            )
        )

    metadata: dict[str, Any] = {}
    assigned = False
    if not questions:
        metadata = set_alias(model_full_name, alias, target_version)
        assigned = True

    return (
        AliasAssignment(
            audit_id=audit_id,
            model_full_name=model_full_name,
            alias=alias,
            target_version=target_version,
            skill_id=skill_id,
            val_floor_passed=val_floor_passed,
            hitl_approved=hitl_approved,
            assigned=assigned,
            metadata=metadata,
        ),
        tuple(questions),
    )


def _question(*, subject: str, details: dict[str, Any], action: str) -> Question:
    return question_from_failure(
        reason=ReasonCode.MODEL_ROLE_NOT_RESOLVED,
        subject=subject,
        raised_by="brickvision_runtime.ml.alias_assigner",
        details=details,
        suggested_next_action=action,
    )


__all__ = ["AliasAssignment", "SetAliasFn", "assign_alias"]
