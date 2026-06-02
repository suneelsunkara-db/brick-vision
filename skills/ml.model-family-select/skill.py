"""Mechanical Layer-0 skill: ``skill:ml.model-family-select``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.model-family-select",
    version="0.1.0",
    dag=DAG(name="ml.model-family-select"),
    constitutional=(
        "model.family.must.follow-problem-and-feature-evidence",
        "model.family.must.not-select-runtime-backend",
        "training.must.not-run-before-model-family-selection",
    ),
)


def run_ml_model_family_select(
    *,
    strategy_plan: dict[str, Any],
    feature_readiness: dict[str, Any],
    dataset_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if strategy_plan.get("status") != "ready_for_approval":
        findings.append(_finding("blocking", "STRATEGY_NOT_READY", "Strategy must be ready_for_approval."))
    if feature_readiness.get("status") != "feature_ready":
        findings.append(_finding("blocking", "FEATURES_NOT_READY", "Feature readiness must pass first."))
    if not dataset_profiles:
        findings.append(_finding("blocking", "DATASET_PROFILE_REQUIRED", "Model family selection needs dataset profiles."))

    problem_type = str(strategy_plan.get("problem_type") or feature_readiness.get("problem_type") or "").strip()
    family = _family_for_problem(problem_type=problem_type, feature_readiness=feature_readiness)
    if family is None:
        findings.append(
            _finding(
                "blocking",
                "NO_MODEL_FAMILY",
                f"No model family is defined for problem_type {problem_type!r}.",
            )
        )

    selected_family = None if _blocking(findings) else family
    return {
        "status": "ready" if selected_family else "blocked",
        "selected_model_family": selected_family,
        "candidate_model_families": [family] if family else [],
        "findings": findings,
        "next_action": (
            "Use selected_model_family in skill:ml.training-backend-select."
            if selected_family
            else "Resolve strategy, feature, or model-family evidence gaps before backend selection."
        ),
    }


def _family_for_problem(
    *,
    problem_type: str,
    feature_readiness: dict[str, Any],
) -> dict[str, Any] | None:
    if problem_type == "regression":
        return _family(
            family_id="tabular_regression",
            problem_type=problem_type,
            candidate_algorithms=("linear_regression", "random_forest_regressor"),
            required_substrate=(
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ),
            label_column=feature_readiness.get("target_column"),
        )
    if problem_type == "classification":
        return _family(
            family_id="tabular_classification",
            problem_type=problem_type,
            candidate_algorithms=("logistic_regression", "random_forest_classifier"),
            required_substrate=(
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ),
            label_column=feature_readiness.get("target_column"),
        )
    if problem_type == "forecasting":
        return _family(
            family_id="time_series_forecasting",
            problem_type=problem_type,
            candidate_algorithms=("statsmodels_forecast",),
            required_substrate=(
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ),
            label_column=feature_readiness.get("target_column"),
        )
    if problem_type == "segmentation":
        return _family(
            family_id="tabular_clustering",
            problem_type=problem_type,
            candidate_algorithms=("kmeans",),
            required_substrate=(
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ),
            label_column=None,
        )
    if problem_type == "anomaly_detection":
        return _family(
            family_id="tabular_anomaly_detection",
            problem_type=problem_type,
            candidate_algorithms=("isolation_forest",),
            required_substrate=(
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ),
            label_column=feature_readiness.get("target_column"),
        )
    if problem_type in {"ranking", "recommendation"}:
        return _family(
            family_id="tabular_ranking",
            problem_type=problem_type,
            candidate_algorithms=("learning_to_rank",),
            required_substrate=(
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ),
            label_column=feature_readiness.get("target_column"),
        )
    if problem_type == "text_or_genai":
        return _family(
            family_id="text_or_genai",
            problem_type=problem_type,
            candidate_algorithms=("mosaic_ai_endpoint_or_rag",),
            required_substrate=("mosaic_ai_available",),
            label_column=None,
        )
    return None


def _family(
    *,
    family_id: str,
    problem_type: str,
    candidate_algorithms: tuple[str, ...],
    required_substrate: tuple[str, ...],
    label_column: object,
) -> dict[str, Any]:
    return {
        "family_id": family_id,
        "problem_type": problem_type,
        "candidate_algorithms": list(candidate_algorithms),
        "required_substrate": list(required_substrate),
        "label_column": str(label_column or "") or None,
        "data_fit": "bounded_tabular_python" if family_id.startswith("tabular_") else "specialized",
    }


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_ml_model_family_select"]
