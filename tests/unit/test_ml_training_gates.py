from collections.abc import Mapping, Sequence
from typing import Any

from brickvision_runtime.ml import (
    ModelRegistration,
    TrainingOutcome,
    TrainingSpec,
    register_model,
    run_training,
)


def _approved_strategy() -> dict[str, Any]:
    return {
        "status": "ready_for_approval",
        "registry": "Unity Catalog Registered Model",
        "uc_model_name": "main.ml.customer_spend_forecast",
        "metric_candidates": ["rmse", "mae"],
    }


def _spec(**overrides: Any) -> TrainingSpec:
    values = {
        "model_id": "main.ml.customer_spend_forecast",
        "strategy_plan": _approved_strategy(),
        "strategy_approval_id": "approval-123",
        "feature_columns": ("txn_count",),
        "label_column": "total_spend",
        "primary_key": "customer_id",
        "transforms": (),
        "val_metric_name": "rmse",
        "val_metric_floor": 10.0,
        "split_seed": 7,
    }
    values.update(overrides)
    return TrainingSpec(**values)


def _rows() -> list[dict[str, Any]]:
    return [
        {"customer_id": f"customer-{idx}", "txn_count": idx, "total_spend": float(idx)}
        for idx in range(20)
    ]


def test_training_blocks_without_approved_strategy() -> None:
    called = False

    def train_fn(
        spec: TrainingSpec,
        train_rows: Sequence[Mapping[str, Any]],
        validation_rows: Sequence[Mapping[str, Any]],
    ) -> TrainingOutcome:
        nonlocal called
        called = True
        return TrainingOutcome("rmse", 9.0, "runs:/model")

    run, questions = run_training(
        spec=_spec(strategy_plan={**_approved_strategy(), "status": "blocked"}),
        rows=_rows(),
        train_fn=train_fn,
        register_model=lambda model_id, artifact, metadata: ModelRegistration(model_id, 1, metadata),
        skill_id="skill:ml.train-evaluate-register",
        audit_id="audit-1",
    )

    assert not called
    assert questions
    assert run.registered_model_name is None


def test_training_registers_only_uc_model_name_after_floor_passes() -> None:
    run, questions = run_training(
        spec=_spec(),
        rows=_rows(),
        train_fn=lambda spec, train_rows, validation_rows: TrainingOutcome("rmse", 9.0, "runs:/model"),
        register_model=lambda model_id, artifact, metadata: ModelRegistration(model_id, 2, metadata),
        skill_id="skill:ml.train-evaluate-register",
        audit_id="audit-1",
    )

    assert not questions
    assert run.val_floor_passed
    assert run.registered_model_name == "main.ml.customer_spend_forecast"
    assert run.registered_model_version == 2


def test_lower_is_better_metric_uses_threshold_ceiling() -> None:
    run, questions = run_training(
        spec=_spec(val_metric_name="rmse", val_metric_floor=10.0),
        rows=_rows(),
        train_fn=lambda spec, train_rows, validation_rows: TrainingOutcome("rmse", 12.0, "runs:/model"),
        register_model=lambda model_id, artifact, metadata: ModelRegistration(model_id, 1, metadata),
        skill_id="skill:ml.train-evaluate-register",
        audit_id="audit-1",
    )

    assert questions
    assert not run.val_floor_passed
    assert run.registered_model_name is None


def test_register_model_rejects_non_uc_name_before_mlflow_call() -> None:
    try:
        register_model("legacy_model", "runs:/model", {})
    except ValueError as exc:
        assert "Unity Catalog" in str(exc)
    else:
        raise AssertionError("register_model accepted a non-UC model name")
