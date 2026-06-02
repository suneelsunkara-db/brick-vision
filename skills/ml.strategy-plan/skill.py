"""Mechanical Layer-0 skill: ``skill:ml.strategy-plan``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.ml.readiness import plan_ml_strategy
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.strategy-plan",
    version="0.1.0",
    dag=DAG(name="ml.strategy-plan"),
    constitutional=(
        "training.must.not.run.before.strategy-approval",
        "registered.model.must.be.unity-catalog",
    ),
)


def run_ml_strategy_plan(
    *,
    problem_selection: dict[str, Any],
    feature_readiness: dict[str, Any],
    dataset_profiles: list[dict[str, Any]],
    model_full_name: str | None = None,
    capability_evidence: list[dict[str, Any]] | None = None,
    api_operations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return plan_ml_strategy(
        problem_selection=problem_selection,
        feature_readiness=feature_readiness,
        dataset_profiles=dataset_profiles,
        model_full_name=model_full_name,
        capability_evidence=capability_evidence,
        api_operations=api_operations,
    )


__all__ = ["SKILL", "run_ml_strategy_plan"]
