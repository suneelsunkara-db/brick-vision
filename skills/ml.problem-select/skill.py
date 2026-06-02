"""Mechanical Layer-0 skill: ``skill:ml.problem-select``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.ml.readiness import select_ml_problem
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.problem-select",
    version="0.1.0",
    dag=DAG(name="ml.problem-select"),
    constitutional=(
        "training.must.not.run.before.problem.selection",
        "model.strategy.must.be.evidence-backed",
    ),
)


def run_ml_problem_select(
    *,
    usecase_title: str,
    business_objective: str,
    dataset_profiles: list[dict[str, Any]],
    candidate_target: str | None = None,
) -> dict[str, Any]:
    return select_ml_problem(
        usecase_title=usecase_title,
        business_objective=business_objective,
        dataset_profiles=dataset_profiles,
        candidate_target=candidate_target,
    )


__all__ = ["SKILL", "run_ml_problem_select"]
