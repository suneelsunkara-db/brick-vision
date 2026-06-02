"""Layer-0 skill: ``skill:ml.train-evaluate-register``.

This skill executes an approved Databricks API plan. It does not implement ML
algorithms, generate training transforms, or run local fallback training.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from brickvision_runtime.harness import (
    BehaviorConstraints,
    Skill,
    SystemPromptSection,
)
from brickvision_runtime.ml import ModelTrainingRun, TrainingSpec
from brickvision_runtime.ml.databricks_training import run_databricks_training_job


SYSTEM_PROMPT_SECTIONS: list[SystemPromptSection] = [
    SystemPromptSection(
        id="role",
        altitude="high",
        text=(
            "You execute only an approved, capability-grounded Databricks API "
            "plan for ML training and Unity Catalog registration."
        ),
    ),
    SystemPromptSection(
        id="schema",
        altitude="high",
        text=(
            "The upstream ML skills must provide api_execution_plan. This skill "
            "submits that plan and reads back the ModelTrainingRun audit row."
        ),
    ),
    SystemPromptSection(
        id="discipline",
        altitude="medium",
        text=(
            "Do not synthesize model code, transforms, metrics, or fallbacks at "
            "execution time. Block if the approved API plan is missing."
        ),
    ),
]


SKILL = Skill.llm_with_tools(
    id="skill:ml.train-evaluate-register",
    version="0.1.0",
    model_role="ml_recipe",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:ml.run_training_job", "tool:ml.register_model"],
    behavior_constraints=BehaviorConstraints(
        must_emit_evidence_chain=True,
        extra={
            "must_validate_metric_against_closed_registry": True,
            "must_emit_model_training_run_audit_row": True,
            "must_split_by_primary_key_hash": True,
            "must_canonicalize_when_spark_mllib": True,  # N148
            "must_resolve_replay_tolerance_per_metric": True,  # N149
            "must_require_approved_strategy_plan": True,
            "must_register_unity_catalog_model": True,
        },
    ),
    max_turns=4,
    constitutional=(
        "no.shell.out",
        "no.network.access",
        "val.metric.must.be.from.closed.registry",
        "train.val.split.must.be.primary.key.hash.deterministic",
        "training.must.not.run.before.strategy-approval",
        "registered.model.must.be.unity-catalog",
    ),
)


CoordinatorCall = Callable[[dict[str, Any]], dict[str, Any]]
TrainingJobSubmitter = Callable[..., ModelTrainingRun]


def run_ml_train_evaluate_register(
    *,
    strategy_plan: Mapping[str, Any],
    model_full_name: str,
    feature_columns: Sequence[str],
    label_column: str,
    primary_key: str,
    rows_uri: str,
    split_seed: int,
    strategy_approval_id: str,
    coordinator_call: CoordinatorCall | None = None,
    submit_training_job: TrainingJobSubmitter = run_databricks_training_job,
    skill_id: str = "skill:ml.train-evaluate-register",
    audit_id: str | None = None,
) -> dict[str, Any]:
    audit_id = audit_id or str(uuid.uuid4())
    _ = coordinator_call
    if not isinstance(strategy_plan.get("api_execution_plan"), Mapping):
        raise ValueError("ML training requires a bound api_execution_plan from skill:ml.api-plan-bind.")

    spec = TrainingSpec(
        model_id=model_full_name,
        strategy_plan=strategy_plan,
        strategy_approval_id=strategy_approval_id,
        feature_columns=tuple(feature_columns),
        label_column=label_column,
        primary_key=primary_key,
        transforms=(),
        val_metric_name=str(strategy_plan.get("selected_metric") or ""),
        val_metric_floor=float(strategy_plan.get("selected_metric_floor") or 0.0),
        split_seed=split_seed,
    )
    run = submit_training_job(
        spec=spec,
        rows_uri=rows_uri,
        skill_id=skill_id,
        audit_id=audit_id,
        api_execution_plan=dict(strategy_plan.get("api_execution_plan") or {}),
    )
    return {
        "model_training_run": run,
        "questions": (),
        "transforms": (),
    }


__all__ = [
    "ModelTrainingRun",
    "SKILL",
    "SYSTEM_PROMPT_SECTIONS",
    "run_ml_train_evaluate_register",
]
