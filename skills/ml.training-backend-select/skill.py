"""Mechanical Layer-0 skill: ``skill:ml.training-backend-select``."""

from __future__ import annotations

from typing import Any

from brickvision_runtime.capability_evidence import (
    has_contract_only_capability_evidence,
    source_grounded_capability_refs,
)
from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.training-backend-select",
    version="0.1.0",
    dag=DAG(name="ml.training-backend-select"),
    constitutional=(
        "training.backend.must.be-capability-proven",
        "training.backend.must.match-runtime-surface",
        "no.unproven.backend-fallback",
    ),
)


def run_ml_training_backend_select(
    *,
    strategy_plan: dict[str, Any],
    feature_readiness: dict[str, Any],
    dataset_profiles: list[dict[str, Any]],
    runtime_surface: str,
    capability_evidence: list[dict[str, Any]],
    model_family: dict[str, Any] | None = None,
    runtime_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    runtime = str(runtime_surface or "").strip() or "unknown"
    problem_type = str(strategy_plan.get("problem_type") or "").strip()
    evidence_refs = _capability_refs(capability_evidence)
    runtime_facts = dict(runtime_evidence or {})
    selected_family = _selected_model_family(model_family)

    if strategy_plan.get("status") != "ready_for_approval":
        findings.append(_finding("blocking", "STRATEGY_NOT_READY", "Strategy must be ready_for_approval."))
    if feature_readiness.get("status") != "feature_ready":
        findings.append(_finding("blocking", "FEATURES_NOT_READY", "Feature readiness must pass first."))
    if not dataset_profiles:
        findings.append(_finding("blocking", "DATASET_PROFILE_REQUIRED", "Backend selection needs dataset profiles."))
    if not evidence_refs:
        findings.append(
            _finding(
                "blocking",
                "CAPABILITY_EVIDENCE_REQUIRED",
                "Backend selection needs capability evidence for candidate Databricks ML backends.",
            )
        )
    if has_contract_only_capability_evidence(capability_evidence):
        findings.append(
            _finding(
                "blocking",
                "HAND_AUTHORED_CAPABILITY_EVIDENCE_REJECTED",
                "Hand-authored skill contracts are not source-grounded ML backend evidence.",
            )
        )

    candidates = [
        _mlflow_flavor_job_backend(
            problem_type=problem_type,
            runtime=runtime,
            refs=evidence_refs,
            selected_family=selected_family,
        ),
        _automl_backend(problem_type=problem_type, runtime=runtime, refs=evidence_refs, runtime_facts=runtime_facts),
        _spark_ml_backend(problem_type=problem_type, runtime=runtime, refs=evidence_refs, runtime_facts=runtime_facts),
        _mosaic_ai_backend(problem_type=problem_type, runtime=runtime, refs=evidence_refs, runtime_facts=runtime_facts),
    ]
    supported = [candidate for candidate in candidates if candidate["status"] == "supported"]
    rejected = [candidate for candidate in candidates if candidate["status"] == "rejected"]
    selected = supported[0] if supported and not _blocking(findings) else None
    if selected is None and not _blocking(findings):
        findings.append(
            _finding(
                "blocking",
                "NO_SUPPORTED_BACKEND",
                "No Databricks-native training backend is proven for this strategy and runtime surface.",
            )
        )
    return {
        "status": "ready" if selected else "blocked",
        "selected_backend": selected,
        "supported_backends": supported,
        "rejected_backends": rejected,
        "findings": findings,
        "next_action": (
            "Bind a driver artifact for the selected backend."
            if selected
            else "Provide capability/runtime evidence for a supported Databricks-native backend."
        ),
    }


def _mlflow_flavor_job_backend(
    *,
    problem_type: str,
    runtime: str,
    refs: tuple[str, ...],
    selected_family: dict[str, Any] | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    family_id = str((selected_family or {}).get("family_id") or "").strip()
    if problem_type not in {
        "classification",
        "regression",
        "anomaly_detection",
    }:
        reasons.append(f"problem_type {problem_type!r} is not an MLflow flavor training artifact target")
    if runtime not in {"serverless_jobs", "classic_job_cluster"}:
        reasons.append(f"runtime_surface {runtime!r} is not a Databricks Jobs runtime")
    if not selected_family:
        reasons.append("model family selection is required before MLflow artifact backend selection")
    if not family_id:
        reasons.append("selected model family is missing family_id")
    if not _has_ref(refs, "mlflow"):
        reasons.append("missing MLflow capability evidence")
    if not (_has_ref(refs, "jobs") or _has_ref(refs, "JobsRunsSubmit")):
        reasons.append("missing Databricks Jobs capability evidence")
    return _backend(
        backend_id="databricks_mlflow_flavor_job",
        runtime_surface=runtime,
        capability_refs=_matching_refs(refs, "mlflow", "jobs", "JobsRunsSubmit"),
        reasons=reasons,
        model_family=selected_family,
    )


def _automl_backend(
    *,
    problem_type: str,
    runtime: str,
    refs: tuple[str, ...],
    runtime_facts: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if problem_type not in {"classification", "regression", "forecasting"}:
        reasons.append(f"problem_type {problem_type!r} is not an AutoML training target")
    if not _has_ref(refs, "automl"):
        reasons.append("missing AutoML capability evidence")
    if runtime == "serverless_jobs" and runtime_facts.get("databricks_automl_available") is not True:
        reasons.append("Databricks AutoML is not available in the observed serverless Jobs runtime")
    return _backend(
        backend_id="databricks_automl",
        runtime_surface=runtime,
        capability_refs=_matching_refs(refs, "automl"),
        reasons=reasons,
    )


def _spark_ml_backend(
    *,
    problem_type: str,
    runtime: str,
    refs: tuple[str, ...],
    runtime_facts: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if problem_type not in {"classification", "regression", "forecasting", "clustering"}:
        reasons.append(f"problem_type {problem_type!r} is not a Spark ML training target")
    if not (_has_ref(refs, "spark") or _has_ref(refs, "mllib")):
        reasons.append("missing Spark ML capability evidence")
    if runtime == "serverless_jobs" and runtime_facts.get("spark_ml_allowed") is not True:
        reasons.append("Spark ML support has not been proven for this serverless Jobs runtime")
    return _backend(
        backend_id="spark_ml",
        runtime_surface=runtime,
        capability_refs=_matching_refs(refs, "spark", "mllib"),
        reasons=reasons,
    )


def _mosaic_ai_backend(
    *,
    problem_type: str,
    runtime: str,
    refs: tuple[str, ...],
    runtime_facts: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if problem_type not in {"text_or_genai", "rag", "embedding"}:
        reasons.append(f"problem_type {problem_type!r} is not a Mosaic AI training/serving target")
    if not (_has_ref(refs, "mosaic") or _has_ref(refs, "serving")):
        reasons.append("missing Mosaic AI capability evidence")
    if runtime_facts.get("mosaic_ai_available") is False:
        reasons.append("Mosaic AI is not available in the observed workspace/runtime")
    return _backend(
        backend_id="mosaic_ai",
        runtime_surface=runtime,
        capability_refs=_matching_refs(refs, "mosaic", "serving"),
        reasons=reasons,
    )


def _backend(
    *,
    backend_id: str,
    runtime_surface: str,
    capability_refs: tuple[str, ...],
    reasons: list[str],
    model_family: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend = {
        "backend_id": backend_id,
        "runtime_surface": runtime_surface,
        "capability_refs": list(capability_refs),
        "status": "rejected" if reasons else "supported",
        "reasons": reasons,
    }
    if model_family:
        backend["model_family"] = model_family
    return backend


def _selected_model_family(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    selected = value.get("selected_model_family")
    if isinstance(selected, dict):
        return dict(selected)
    if value.get("family_id"):
        return dict(value)
    return None


def _runtime_fact(runtime_facts: dict[str, Any], dotted_path: str) -> bool:
    current: Any = runtime_facts
    if not isinstance(current, dict):
        return False
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return False
        if part in current:
            current = current[part]
            continue
        substrate = current.get("substrate")
        if isinstance(substrate, dict) and part in substrate:
            current = substrate[part]
            continue
        return False
    return current is True


def _capability_refs(items: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(source_grounded_capability_refs(item for item in items if isinstance(item, dict)))


def _has_ref(refs: tuple[str, ...], needle: str) -> bool:
    lowered = needle.lower()
    return any(lowered in ref.lower() for ref in refs)


def _matching_refs(refs: tuple[str, ...], *needles: str) -> tuple[str, ...]:
    lowered = tuple(needle.lower() for needle in needles)
    return tuple(ref for ref in refs if any(needle in ref.lower() for needle in lowered))


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_ml_training_backend_select"]
