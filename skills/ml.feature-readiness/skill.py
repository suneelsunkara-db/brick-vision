"""Mechanical Layer-0 skill: ``skill:ml.feature-readiness``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.ml.readiness import assess_feature_readiness
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.feature-readiness",
    version="0.1.0",
    dag=DAG(name="ml.feature-readiness"),
    constitutional=(
        "training.must.not.run.before.feature-readiness",
        "label.provenance.must.be.explicit",
    ),
)


def run_ml_feature_readiness(
    *,
    problem_type: str,
    dataset_profiles: list[dict[str, Any]],
    target_column: str | None = None,
    entity_key: str | None = None,
    time_column: str | None = None,
) -> dict[str, Any]:
    return assess_feature_readiness(
        problem_type=problem_type,
        dataset_profiles=dataset_profiles,
        target_column=target_column,
        entity_key=entity_key,
        time_column=time_column,
    )


__all__ = ["SKILL", "run_ml_feature_readiness"]
