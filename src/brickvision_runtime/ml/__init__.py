"""ML runtime contracts used by BrickVision ML skills."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from brickvision_runtime.failures import Question, ReasonCode, question_from_failure


@dataclasses.dataclass(frozen=True)
class FeatureTransform:
    name: str
    column: str
    arg: float = 0.0


@dataclasses.dataclass(frozen=True)
class TrainingSpec:
    model_id: str
    strategy_plan: Mapping[str, Any]
    strategy_approval_id: str
    feature_columns: tuple[str, ...]
    label_column: str
    primary_key: str
    transforms: tuple[FeatureTransform, ...]
    val_metric_name: str
    val_metric_floor: float
    split_seed: int


@dataclasses.dataclass(frozen=True)
class ModelTrainingRun:
    audit_id: str
    model_id: str
    skill_id: str
    val_metric_name: str
    val_metric_value: float
    val_metric_floor: float
    val_floor_passed: bool
    train_row_count: int
    validation_row_count: int
    registered_model_name: str | None
    registered_model_version: int | None
    feature_set_hash: str


@dataclasses.dataclass(frozen=True)
class TrainingOutcome:
    val_metric_name: str
    val_metric_value: float
    model_artifact: Any
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ModelRegistration:
    model_full_name: str
    version: int
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


TrainFn = Callable[[TrainingSpec, Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], TrainingOutcome]
RegisterModelFn = Callable[[str, Any, dict[str, Any]], ModelRegistration]

_VALID_METRICS = {
    "accuracy",
    "f1",
    "f1_macro",
    "precision",
    "precision_at_k",
    "recall",
    "recall_at_k",
    "rmse",
    "mae",
    "mape",
    "r2",
    "auc",
    "roc_auc",
    "log_loss",
    "ndcg_at_k",
    "map_at_k",
}
_LOWER_IS_BETTER_METRICS = {"rmse", "mae", "mape", "log_loss", "false_positive_rate"}
_VALID_TRANSFORMS = {"log1p", "zscore", "clip", "one_hot"}


def run_training(
    *,
    spec: TrainingSpec,
    rows: Sequence[Mapping[str, Any]],
    train_fn: TrainFn,
    register_model: RegisterModelFn,
    skill_id: str,
    audit_id: str,
) -> tuple[ModelTrainingRun, tuple[Question, ...]]:
    questions = tuple(_validate_training_spec(spec))
    train_rows, validation_rows = _deterministic_split(rows, spec.primary_key, spec.split_seed)

    if questions:
        floor_passed = False
        outcome = TrainingOutcome(
            val_metric_name=spec.val_metric_name,
            val_metric_value=float("-inf"),
            model_artifact=None,
        )
        registration = None
    else:
        outcome = train_fn(spec, train_rows, validation_rows)
        floor_passed = _metric_threshold_passed(
            metric_name=outcome.val_metric_name,
            metric_value=outcome.val_metric_value,
            threshold=spec.val_metric_floor,
        )
        registration = (
            register_model(
                spec.model_id,
                outcome.model_artifact,
                {
                    "audit_id": audit_id,
                    "val_metric_name": outcome.val_metric_name,
                    "val_metric_value": outcome.val_metric_value,
                    "val_floor_passed": floor_passed,
                    "feature_set_hash": _feature_set_hash(spec),
                },
            )
            if floor_passed
            else None
        )
        if not floor_passed:
            questions = (
                _question(
                    subject=spec.model_id,
                    details={
                        "val_metric_name": outcome.val_metric_name,
                        "val_metric_value": outcome.val_metric_value,
                        "val_metric_floor": spec.val_metric_floor,
                    },
                    action="Improve the feature set or lower the explicitly approved validation floor.",
                ),
            )

    run = ModelTrainingRun(
        audit_id=audit_id,
        model_id=spec.model_id,
        skill_id=skill_id,
        val_metric_name=outcome.val_metric_name,
        val_metric_value=outcome.val_metric_value,
        val_metric_floor=spec.val_metric_floor,
        val_floor_passed=floor_passed,
        train_row_count=len(train_rows),
        validation_row_count=len(validation_rows),
        registered_model_name=registration.model_full_name if registration else None,
        registered_model_version=registration.version if registration else None,
        feature_set_hash=_feature_set_hash(spec),
    )
    return run, questions


def register_model(model_id: str, model_artifact: Any, metadata: dict[str, Any]) -> ModelRegistration:
    """Register a model artifact as a Unity Catalog registered model."""

    if not _is_uc_model_name(model_id):
        raise ValueError("model_id must be a Unity Catalog three-part name: catalog.schema.model")

    import mlflow  # type: ignore[import-not-found]

    mlflow.set_registry_uri("databricks-uc")
    result = mlflow.register_model(model_uri=str(model_artifact), name=model_id)
    version = int(getattr(result, "version", 0) or 0)
    return ModelRegistration(
        model_full_name=str(getattr(result, "name", model_id)),
        version=version,
        metadata=dict(metadata),
    )


def _validate_training_spec(spec: TrainingSpec) -> list[Question]:
    questions: list[Question] = []
    questions.extend(_validate_strategy_gate(spec))
    if spec.val_metric_name not in _VALID_METRICS:
        questions.append(
            _question(
                subject=spec.model_id,
                details={"val_metric_name": spec.val_metric_name},
                action="Use a validation metric from the closed BrickVision metric registry.",
            )
        )
    invalid_transforms = sorted({t.name for t in spec.transforms} - _VALID_TRANSFORMS)
    if invalid_transforms:
        questions.append(
            _question(
                subject=spec.model_id,
                details={"invalid_transforms": invalid_transforms},
                action="Use only closed-registry feature transforms.",
            )
        )
    return questions


def _validate_strategy_gate(spec: TrainingSpec) -> list[Question]:
    questions: list[Question] = []
    strategy_status = str(spec.strategy_plan.get("status") or "")
    registry = str(spec.strategy_plan.get("registry") or "")
    strategy_model_name = str(spec.strategy_plan.get("uc_model_name") or "")
    if strategy_status != "ready_for_approval":
        questions.append(
            _question(
                subject=spec.model_id,
                details={"strategy_status": strategy_status},
                action="Run and approve skill:ml.strategy-plan before training.",
            )
        )
    if not spec.strategy_approval_id.strip():
        questions.append(
            _question(
                subject=spec.model_id,
                details={"strategy_approval_id": spec.strategy_approval_id},
                action="Bind the strategy approval audit id before training.",
            )
        )
    if registry != "Unity Catalog Registered Model":
        questions.append(
            _question(
                subject=spec.model_id,
                details={"registry": registry},
                action="Use Unity Catalog Registered Model as the registration target.",
            )
        )
    if not _is_uc_model_name(spec.model_id):
        questions.append(
            _question(
                subject=spec.model_id,
                details={"model_full_name": spec.model_id},
                action="Bind model_full_name as catalog.schema.model.",
            )
        )
    if strategy_model_name and strategy_model_name != spec.model_id:
        questions.append(
            _question(
                subject=spec.model_id,
                details={"strategy_model_name": strategy_model_name, "model_full_name": spec.model_id},
                action="Train only against the UC model name approved in the strategy plan.",
            )
        )
    return questions


def _deterministic_split(
    rows: Sequence[Mapping[str, Any]],
    primary_key: str,
    split_seed: int,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    train: list[Mapping[str, Any]] = []
    validation: list[Mapping[str, Any]] = []
    for row in rows:
        raw_key = f"{split_seed}:{row.get(primary_key, '')}"
        bucket = int(hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:8], 16) % 100
        (validation if bucket < 20 else train).append(row)
    return tuple(train), tuple(validation)


def _feature_set_hash(spec: TrainingSpec) -> str:
    raw = "|".join(
        [
            spec.model_id,
            ",".join(spec.feature_columns),
            spec.label_column,
            ",".join(f"{t.name}:{t.column}:{t.arg}" for t in spec.transforms),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _metric_threshold_passed(*, metric_name: str, metric_value: float, threshold: float) -> bool:
    if metric_name in _LOWER_IS_BETTER_METRICS:
        return metric_value <= threshold
    return metric_value >= threshold


def _is_uc_model_name(model_full_name: str) -> bool:
    return len([part for part in model_full_name.split(".") if part.strip()]) == 3


def _question(*, subject: str, details: dict[str, Any], action: str) -> Question:
    return question_from_failure(
        reason=ReasonCode.MODEL_ROLE_NOT_RESOLVED,
        subject=subject,
        raised_by="brickvision_runtime.ml",
        details=details,
        suggested_next_action=action,
    )


__all__ = [
    "FeatureTransform",
    "ModelRegistration",
    "ModelTrainingRun",
    "RegisterModelFn",
    "TrainFn",
    "TrainingOutcome",
    "TrainingSpec",
    "register_model",
    "run_training",
]
