"""Generic ML opportunity and feature-readiness heuristics.

These primitives deliberately do not train models. They decide whether a
business usecase and candidate datasets are ready for supervised, unsupervised,
forecasting, ranking/recommendation, anomaly, text/GenAI, or no-ML paths.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class ColumnProfile:
    name: str
    data_type: str
    distinct_count: int | None = None
    null_count: int | None = None


@dataclasses.dataclass(frozen=True)
class DatasetProfile:
    table_ref: str
    row_count: int | None
    columns: tuple[ColumnProfile, ...]


@dataclasses.dataclass(frozen=True)
class MlReadinessFinding:
    severity: str
    code: str
    message: str


@dataclasses.dataclass(frozen=True)
class MlProblemSelection:
    status: str
    recommended_problem_type: str
    recommended_strategy: str
    target_column: str | None
    entity_keys: tuple[str, ...]
    time_columns: tuple[str, ...]
    feature_columns: tuple[str, ...]
    findings: tuple[MlReadinessFinding, ...]
    next_action: str


@dataclasses.dataclass(frozen=True)
class MlStrategyPlan:
    status: str
    problem_type: str
    databricks_path: str
    training_compute: str
    registry: str
    uc_model_name: str | None
    serving_path: str
    required_bindings: tuple[str, ...]
    metric_candidates: tuple[str, ...]
    split_policy: str
    capability_refs: tuple[str, ...]
    api_execution_plan: dict[str, Any] | None
    findings: tuple[MlReadinessFinding, ...]
    next_action: str


_TARGET_HINTS = (
    "label",
    "target",
    "outcome",
    "churn",
    "fraud",
    "accepted",
    "converted",
    "response",
    "impact",
    "score",
    "amount",
    "spend",
    "risk",
)
_KEY_HINTS = ("id", "key")
_TIME_HINTS = ("date", "time", "timestamp", "ts", "month", "year")
_TEXT_HINTS = ("text", "message", "description", "notes", "content", "document")


def select_ml_problem(
    *,
    usecase_title: str,
    business_objective: str,
    dataset_profiles: list[dict[str, Any]],
    candidate_target: str | None = None,
) -> dict[str, Any]:
    profiles = _profiles(dataset_profiles)
    findings = _base_findings(profiles)
    columns = [column for profile in profiles for column in profile.columns]
    explicit_target = bool(candidate_target)
    target = candidate_target or _candidate_target(columns)
    entity_keys = tuple(sorted({column.name for column in columns if _is_entity_key(column.name)}))
    time_columns = tuple(sorted({column.name for column in columns if _is_time_column(column)}))
    feature_columns = tuple(
        sorted(
            {
                column.name
                for column in columns
                if column.name != target and _is_feature_column(column)
            }
        )
    )
    problem_type, strategy = _problem_and_strategy(
        usecase_title=usecase_title,
        business_objective=business_objective,
        target=target,
        columns=columns,
        time_columns=time_columns,
    )
    findings.extend(
        _problem_findings(
            problem_type=problem_type,
            target=target,
            entity_keys=entity_keys,
            time_columns=time_columns,
            feature_columns=feature_columns,
            profiles=profiles,
            explicit_target=explicit_target,
        )
    )
    status = "ready_for_strategy" if not _blocking(findings) else "needs_more_evidence"
    return dataclasses.asdict(
        MlProblemSelection(
            status=status,
            recommended_problem_type=problem_type,
            recommended_strategy=strategy,
            target_column=target,
            entity_keys=entity_keys,
            time_columns=time_columns,
            feature_columns=feature_columns[:50],
            findings=tuple(findings),
            next_action=(
                "Review and approve the ML strategy before training."
                if status == "ready_for_strategy"
                else "Resolve ML evidence gaps before selecting a training path."
            ),
        )
    )


def assess_feature_readiness(
    *,
    problem_type: str,
    dataset_profiles: list[dict[str, Any]],
    target_column: str | None = None,
    entity_key: str | None = None,
    time_column: str | None = None,
) -> dict[str, Any]:
    profiles = _profiles(dataset_profiles)
    columns = [column for profile in profiles for column in profile.columns]
    findings = _base_findings(profiles)
    if problem_type in {"classification", "regression", "ranking", "forecasting"} and not target_column:
        findings.append(
            MlReadinessFinding(
                "blocking",
                "TARGET_REQUIRED",
                f"{problem_type} needs an explicit target or label column.",
            )
        )
    if problem_type in {"ranking", "recommendation"} and not _is_response_label(target_column):
        findings.append(
            MlReadinessFinding(
                "blocking",
                "RESPONSE_LABEL_REQUIRED",
                "Ranking/recommendation needs an observed response, acceptance, conversion, or reward label.",
            )
        )
    if entity_key is None and any(_is_entity_key(column.name) for column in columns):
        entity_key = next(column.name for column in columns if _is_entity_key(column.name))
    if problem_type in {"ranking", "recommendation", "forecasting"} and not entity_key:
        findings.append(
            MlReadinessFinding(
                "blocking",
                "ENTITY_KEY_REQUIRED",
                f"{problem_type} needs a stable entity key.",
            )
        )
    if time_column is None and any(_is_time_column(column) for column in columns):
        time_column = next(column.name for column in columns if _is_time_column(column))
    if problem_type == "forecasting" and not time_column:
        findings.append(
            MlReadinessFinding(
                "blocking",
                "TIME_COLUMN_REQUIRED",
                "Forecasting needs a stable time column and forecast horizon.",
            )
        )
    if target_column and _looks_generated_or_leaky(target_column):
        findings.append(
            MlReadinessFinding(
                "warning",
                "POTENTIAL_LABEL_LEAKAGE",
                f"{target_column} looks generated or derived; confirm label provenance.",
            )
        )
    status = "feature_ready" if not _blocking(findings) else "needs_more_evidence"
    return {
        "status": status,
        "problem_type": problem_type,
        "target_column": target_column,
        "entity_key": entity_key,
        "time_column": time_column,
        "findings": [dataclasses.asdict(item) for item in findings],
        "next_action": (
            "Proceed to model strategy approval."
            if status == "feature_ready"
            else "Define the missing label, entity, time, or feature evidence first."
        ),
    }


def plan_ml_strategy(
    *,
    problem_selection: dict[str, Any],
    feature_readiness: dict[str, Any],
    dataset_profiles: list[dict[str, Any]],
    model_full_name: str | None = None,
    capability_evidence: list[dict[str, Any]] | None = None,
    api_operations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a Databricks-native strategy plan after readiness checks pass."""

    profiles = _profiles(dataset_profiles)
    problem_type = str(problem_selection.get("recommended_problem_type") or "").strip()
    findings = _base_findings(profiles)
    findings.extend(_findings_from_payload(problem_selection))
    findings.extend(_findings_from_payload(feature_readiness))
    if problem_selection.get("status") != "ready_for_strategy":
        findings.append(
            MlReadinessFinding(
                "blocking",
                "PROBLEM_SELECTION_NOT_READY",
                "Problem selection must be ready before creating a training strategy.",
            )
        )
    if feature_readiness.get("status") != "feature_ready":
        findings.append(
            MlReadinessFinding(
                "blocking",
                "FEATURES_NOT_READY",
                "Feature readiness must pass before creating a training strategy.",
            )
        )
    required_bindings = _required_training_bindings(problem_type)
    if model_full_name and not _is_uc_model_name(model_full_name):
        findings.append(
            MlReadinessFinding(
                "blocking",
                "UC_MODEL_NAME_REQUIRED",
                "model_full_name must be a Unity Catalog three-part name: catalog.schema.model.",
            )
        )
    if not model_full_name and problem_type not in {"no_ml_yet", "text_or_genai"}:
        required_bindings = (*required_bindings, "model_full_name")
        findings.append(
            MlReadinessFinding(
                "warning",
                "UC_MODEL_NAME_NOT_BOUND",
                "Training can be planned, but UC model registration needs a catalog.schema.model name.",
            )
        )
    if problem_type in {"no_ml_yet", ""}:
        findings.append(
            MlReadinessFinding(
                "blocking",
                "NO_TRAINABLE_STRATEGY",
                "No trainable ML strategy should run until the objective and evidence improve.",
            )
        )
    capability_refs = _capability_refs(capability_evidence or [])
    api_execution_plan = _api_execution_plan(
        problem_type=problem_type,
        model_full_name=model_full_name,
        capability_refs=capability_refs,
        api_operations=api_operations or [],
    )
    status = "ready_for_approval" if not _blocking(findings) else "blocked"
    if status == "ready_for_approval" and api_execution_plan is None:
        required_bindings = (*required_bindings, "api_execution_plan")
        findings.append(
            MlReadinessFinding(
                "warning",
                "CAPABILITY_GROUNDED_API_PLAN_REQUIRED",
                (
                    "Training strategy is ready for approval, but execution still needs "
                    "API operations grounded in indexed SDK/OpenAPI/docs evidence."
                ),
            )
        )
    return dataclasses.asdict(
        MlStrategyPlan(
            status=status,
            problem_type=problem_type or "unknown",
            databricks_path=_databricks_path(problem_type),
            training_compute="Databricks Jobs or Serverless compute with Unity Catalog access",
            registry="Unity Catalog Registered Model",
            uc_model_name=model_full_name,
            serving_path=_serving_path(problem_type),
            required_bindings=required_bindings,
            metric_candidates=_metric_candidates(problem_type),
            split_policy=_split_policy(problem_type),
            capability_refs=capability_refs,
            api_execution_plan=api_execution_plan,
            findings=tuple(findings),
            next_action=(
                "Approve this strategy, bind UC model name and training parameters, then run training."
                if status == "ready_for_approval"
                else "Resolve strategy blockers before training or model registration."
            ),
        )
    )


def _profiles(raw_profiles: list[dict[str, Any]]) -> list[DatasetProfile]:
    profiles: list[DatasetProfile] = []
    for raw in raw_profiles:
        columns_raw = raw.get("columns") if isinstance(raw, dict) else []
        columns: list[ColumnProfile] = []
        if isinstance(columns_raw, dict):
            iterable = [
                {"name": name, "data_type": data_type}
                for name, data_type in columns_raw.items()
            ]
        elif isinstance(columns_raw, list):
            iterable = columns_raw
        else:
            iterable = []
        for item in iterable:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            columns.append(
                ColumnProfile(
                    name=name,
                    data_type=str(item.get("data_type") or item.get("type") or "").strip(),
                    distinct_count=_int_or_none(item.get("distinct_count")),
                    null_count=_int_or_none(item.get("null_count")),
                )
            )
        profiles.append(
            DatasetProfile(
                table_ref=str(raw.get("table_ref") or raw.get("name") or "").strip(),
                row_count=_int_or_none(raw.get("row_count")),
                columns=tuple(columns),
            )
        )
    return profiles


def _findings_from_payload(payload: dict[str, Any]) -> list[MlReadinessFinding]:
    findings: list[MlReadinessFinding] = []
    raw_findings = payload.get("findings") if isinstance(payload, dict) else []
    if not isinstance(raw_findings, list):
        return findings
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity") or "").strip()
        code = str(raw.get("code") or "").strip()
        message = str(raw.get("message") or "").strip()
        if severity and code and message:
            findings.append(MlReadinessFinding(severity, code, message))
    return findings


def _capability_refs(capability_evidence: list[dict[str, Any]]) -> tuple[str, ...]:
    refs: set[str] = set()
    for item in capability_evidence:
        if not isinstance(item, dict):
            continue
        for key in ("entity_id", "id", "operation_id", "capability_ref"):
            value = str(item.get(key) or "").strip()
            if _is_indexed_capability_ref(value):
                refs.add(value)
        nested = item.get("capability_refs")
        if isinstance(nested, list):
            refs.update(str(value).strip() for value in nested if _is_indexed_capability_ref(str(value).strip()))
    return tuple(sorted(refs))


def _api_execution_plan(
    *,
    problem_type: str,
    model_full_name: str | None,
    capability_refs: tuple[str, ...],
    api_operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not model_full_name or not capability_refs or not api_operations:
        return None
    operations = [_api_operation(item) for item in api_operations]
    operations = [item for item in operations if item is not None]
    if not operations:
        return None
    audit_readback = next(
        (item for item in operations if "statement" in item["path"].lower() or "audit" in item["operation_id"].lower()),
        None,
    )
    executable_operations = [item for item in operations if item is not audit_readback]
    if not executable_operations or audit_readback is None:
        return None
    return {
        "plan_id": f"ml-{problem_type}-capability-plan",
        "capability_refs": list(capability_refs),
        "operations": executable_operations,
        "audit_readback": {"operation": audit_readback},
    }


def _api_operation(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    operation_id = str(raw.get("operation_id") or raw.get("entity_id") or raw.get("id") or "").strip()
    method = str(raw.get("method") or raw.get("http_method") or "").upper().strip()
    path = str(raw.get("path") or raw.get("api_path") or "").strip()
    refs = _capability_refs([raw])
    if not refs and _is_indexed_capability_ref(operation_id):
        refs = (operation_id,)
    if not operation_id or method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not path or not refs:
        return None
    body = raw.get("body")
    if method != "GET" and (not isinstance(body, dict) or not body):
        return None
    return {
        "operation_id": operation_id,
        "method": method,
        "path": path,
        "body": dict(body or {}),
        "capability_refs": list(refs),
    }


def _is_indexed_capability_ref(value: str) -> bool:
    return value.startswith(("sdk:", "openapi:", "docs:", "doc:", "meta:", "ext:"))


def _base_findings(profiles: list[DatasetProfile]) -> list[MlReadinessFinding]:
    findings: list[MlReadinessFinding] = []
    if not profiles:
        findings.append(MlReadinessFinding("blocking", "DATASET_REQUIRED", "At least one dataset profile is required."))
        return findings
    total_rows = sum(profile.row_count or 0 for profile in profiles)
    if total_rows < 100:
        findings.append(
            MlReadinessFinding(
                "blocking",
                "INSUFFICIENT_ROWS",
                f"Only {total_rows} rows observed; most ML paths need more evidence.",
            )
        )
    if not any(profile.columns for profile in profiles):
        findings.append(MlReadinessFinding("blocking", "COLUMN_PROFILE_REQUIRED", "Column profiles are required."))
    return findings


def _problem_and_strategy(
    *,
    usecase_title: str,
    business_objective: str,
    target: str | None,
    columns: list[ColumnProfile],
    time_columns: tuple[str, ...],
) -> tuple[str, str]:
    text = f"{usecase_title} {business_objective}".lower()
    if any(word in text for word in ("forecast", "trend", "next month", "future")) and time_columns:
        return "forecasting", "Databricks-native time-series forecasting or Spark feature pipeline"
    if any(word in text for word in ("recommend", "next best", "rank", "personal")):
        return "ranking", "ranking/recommendation readiness; train only when response labels exist"
    if any(word in text for word in ("segment", "cluster", "cohort")):
        return "segmentation", "Spark ML clustering or feature table segmentation"
    if any(word in text for word in ("anomaly", "outlier", "unusual")):
        return "anomaly_detection", "Spark anomaly scoring or rules-plus-ML validation"
    if any(_is_text_column(column) for column in columns) and any(word in text for word in ("rag", "agent", "text", "document")):
        return "text_or_genai", "Mosaic AI/Vector Search/RAG evaluation path, not tabular ML"
    if target:
        if _is_numeric_name(target) or _column_type(target, columns) in {"numeric", "decimal"}:
            return "regression", "Databricks AutoML or Spark ML regression with UC registered model"
        return "classification", "Databricks AutoML or Spark ML classification with UC registered model"
    return "no_ml_yet", "Build feature/label readiness before training"


def _problem_findings(
    *,
    problem_type: str,
    target: str | None,
    entity_keys: tuple[str, ...],
    time_columns: tuple[str, ...],
    feature_columns: tuple[str, ...],
    profiles: list[DatasetProfile],
    explicit_target: bool,
) -> list[MlReadinessFinding]:
    findings: list[MlReadinessFinding] = []
    if problem_type in {"classification", "regression", "ranking", "forecasting"} and not target:
        findings.append(MlReadinessFinding("blocking", "TARGET_REQUIRED", f"{problem_type} needs a target label."))
    if problem_type == "ranking" and (not explicit_target or not _is_response_label(target)):
        findings.append(
            MlReadinessFinding(
                "blocking",
                "RESPONSE_LABEL_REQUIRED",
                "Recommendation/ranking needs an explicit observed response, acceptance, conversion, or reward label.",
            )
        )
    if problem_type in {"ranking", "recommendation", "forecasting"} and not entity_keys:
        findings.append(MlReadinessFinding("blocking", "ENTITY_KEY_REQUIRED", f"{problem_type} needs an entity key."))
    if problem_type == "forecasting" and not time_columns:
        findings.append(MlReadinessFinding("blocking", "TIME_COLUMN_REQUIRED", "Forecasting needs a time column."))
    if len(feature_columns) < 2 and problem_type not in {"no_ml_yet", "text_or_genai"}:
        findings.append(MlReadinessFinding("warning", "LOW_FEATURE_COUNT", "Few usable features were detected."))
    if problem_type == "no_ml_yet":
        findings.append(
            MlReadinessFinding(
                "blocking",
                "MODEL_OBJECTIVE_REQUIRED",
                "No defensible ML objective or target was detected from the usecase and data.",
            )
        )
    if any((profile.row_count or 0) > 0 for profile in profiles) and problem_type == "segmentation":
        findings.append(
            MlReadinessFinding(
                "info",
                "LABEL_NOT_REQUIRED",
                "Segmentation can proceed without a supervised label if features are approved.",
            )
        )
    return findings


def _candidate_target(columns: list[ColumnProfile]) -> str | None:
    scored: list[tuple[int, str]] = []
    for column in columns:
        name = column.name.lower()
        score = sum(1 for hint in _TARGET_HINTS if hint in name)
        if score:
            scored.append((score, column.name))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def _is_entity_key(name: str) -> bool:
    lower = name.lower()
    return any(hint in lower for hint in _KEY_HINTS) and not _is_time_name(lower)


def _is_time_column(column: ColumnProfile) -> bool:
    lower_type = column.data_type.lower()
    return _is_time_name(column.name.lower()) or any(token in lower_type for token in ("date", "time", "timestamp"))


def _is_time_name(lower_name: str) -> bool:
    return any(hint in lower_name for hint in _TIME_HINTS)


def _is_text_column(column: ColumnProfile) -> bool:
    lower_name = column.name.lower()
    lower_type = column.data_type.lower()
    return any(hint in lower_name for hint in _TEXT_HINTS) or lower_type in {"string", "varchar"}


def _is_feature_column(column: ColumnProfile) -> bool:
    return not column.name.startswith("_")


def _is_numeric_name(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in ("amount", "spend", "score", "impact", "count", "rate", "value"))


def _column_type(name: str, columns: list[ColumnProfile]) -> str:
    for column in columns:
        if column.name == name:
            data_type = column.data_type.lower()
            if any(token in data_type for token in ("int", "double", "float", "decimal", "numeric", "bigint")):
                return "numeric"
    return "other"


def _looks_generated_or_leaky(column_name: str) -> bool:
    lower = column_name.lower()
    return any(token in lower for token in ("score", "confidence", "estimated", "generated", "prediction"))


def _is_response_label(column_name: str | None) -> bool:
    if not column_name:
        return False
    lower = column_name.lower()
    return any(
        token in lower
        for token in (
            "accepted",
            "acceptance",
            "converted",
            "conversion",
            "clicked",
            "response",
            "responded",
            "reward",
            "purchased",
            "selected",
        )
    )


def _blocking(findings: list[MlReadinessFinding]) -> bool:
    return any(finding.severity == "blocking" for finding in findings)


def _required_training_bindings(problem_type: str) -> tuple[str, ...]:
    common = ("feature_table_or_training_set", "evaluation_metric", "split_policy")
    if problem_type in {"classification", "regression"}:
        return (*common, "target_column", "primary_key")
    if problem_type == "forecasting":
        return (*common, "target_column", "time_column", "entity_key", "forecast_horizon")
    if problem_type == "ranking":
        return (*common, "response_label", "entity_key", "candidate_item_key")
    if problem_type == "segmentation":
        return ("feature_table_or_training_set", "cluster_count_or_selection_policy", "primary_key")
    if problem_type == "anomaly_detection":
        return ("feature_table_or_training_set", "entity_key", "threshold_policy")
    if problem_type == "text_or_genai":
        return ("evaluation_set", "retrieval_corpus_or_endpoint", "quality_metrics")
    return ()


def _databricks_path(problem_type: str) -> str:
    return {
        "classification": "Databricks AutoML or Spark ML classification on Databricks compute",
        "regression": "Databricks AutoML or Spark ML regression on Databricks compute",
        "forecasting": "Databricks-native forecasting with time/entity-aware splits",
        "ranking": "Spark ranking/recommendation pipeline with response-label evaluation",
        "segmentation": "Spark ML clustering with feature table materialization",
        "anomaly_detection": "Spark anomaly scoring with threshold calibration",
        "text_or_genai": "Mosaic AI, Vector Search, and RAG/agent evaluation path",
    }.get(problem_type, "No Databricks training path selected")


def _serving_path(problem_type: str) -> str:
    if problem_type == "text_or_genai":
        return "Mosaic AI endpoint or agent deployment after evaluation approval"
    if problem_type in {"classification", "regression", "forecasting", "ranking", "anomaly_detection"}:
        return "Mosaic AI Model Serving endpoint after UC model registration and alias approval"
    if problem_type == "segmentation":
        return "Delta feature table or scheduled scoring job; serving endpoint only if online scoring is needed"
    return "No serving path until strategy is trainable"


def _metric_candidates(problem_type: str) -> tuple[str, ...]:
    return {
        "classification": ("roc_auc", "f1", "accuracy", "log_loss"),
        "regression": ("rmse", "mae", "r2"),
        "forecasting": ("rmse", "mae", "mape"),
        "ranking": ("ndcg_at_k", "map_at_k", "precision_at_k"),
        "segmentation": ("silhouette", "cluster_stability"),
        "anomaly_detection": ("precision_at_k", "recall_at_k", "false_positive_rate"),
        "text_or_genai": ("answer_correctness", "groundedness", "retrieval_recall"),
    }.get(problem_type, ())


def _split_policy(problem_type: str) -> str:
    if problem_type == "forecasting":
        return "time-based backtest split; no random leakage across forecast horizon"
    if problem_type == "ranking":
        return "entity/time-aware split with held-out response events"
    if problem_type in {"classification", "regression"}:
        return "stratified or random split unless time/entity leakage is detected"
    if problem_type == "segmentation":
        return "fit on approved feature window; validate stability on holdout window"
    return "evaluation set defined by strategy owner"


def _is_uc_model_name(model_full_name: str) -> bool:
    return len([part for part in model_full_name.split(".") if part.strip()]) == 3


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
