from brickvision_runtime.ml.readiness import (
    assess_feature_readiness,
    plan_ml_strategy,
    select_ml_problem,
)


def _customer_spend_profile() -> list[dict[str, object]]:
    return [
        {
            "table_ref": "demo.customer_spend",
            "row_count": 1000,
            "columns": [
                {"name": "customer_id", "data_type": "string"},
                {"name": "month", "data_type": "string"},
                {"name": "total_spend", "data_type": "decimal(28,2)"},
                {"name": "txn_count", "data_type": "bigint"},
                {"name": "category", "data_type": "string"},
            ],
        }
    ]


def test_forecasting_with_explicit_target_is_strategy_ready() -> None:
    result = select_ml_problem(
        usecase_title="Forecast customer monthly spend",
        business_objective="Predict future spend by customer",
        dataset_profiles=_customer_spend_profile(),
        candidate_target="total_spend",
    )

    assert result["status"] == "ready_for_strategy"
    assert result["recommended_problem_type"] == "forecasting"
    assert result["target_column"] == "total_spend"


def test_feature_readiness_accepts_supervised_regression_evidence() -> None:
    result = assess_feature_readiness(
        problem_type="regression",
        dataset_profiles=_customer_spend_profile(),
        target_column="total_spend",
        entity_key="customer_id",
    )

    assert result["status"] == "feature_ready"


def test_recommendation_without_observed_response_label_is_blocked() -> None:
    result = select_ml_problem(
        usecase_title="Personalized spend recommendation model",
        business_objective="Recommend next best spend action",
        dataset_profiles=_customer_spend_profile(),
    )

    assert result["status"] == "needs_more_evidence"
    assert result["recommended_problem_type"] == "ranking"
    assert "RESPONSE_LABEL_REQUIRED" in {
        finding["code"] for finding in result["findings"]
    }


def test_strategy_plan_uses_unity_catalog_registered_model() -> None:
    problem = select_ml_problem(
        usecase_title="Forecast customer monthly spend",
        business_objective="Predict future spend by customer",
        dataset_profiles=_customer_spend_profile(),
        candidate_target="total_spend",
    )
    readiness = assess_feature_readiness(
        problem_type="forecasting",
        dataset_profiles=_customer_spend_profile(),
        target_column="total_spend",
        entity_key="customer_id",
        time_column="month",
    )

    plan = plan_ml_strategy(
        problem_selection=problem,
        feature_readiness=readiness,
        dataset_profiles=_customer_spend_profile(),
        model_full_name="main.ml.customer_spend_forecast",
    )

    assert plan["status"] == "ready_for_approval"
    assert plan["registry"] == "Unity Catalog Registered Model"
    assert plan["uc_model_name"] == "main.ml.customer_spend_forecast"
    assert "forecast_horizon" in plan["required_bindings"]
    assert "api_execution_plan" in plan["required_bindings"]
    assert plan["api_execution_plan"] is None


def test_strategy_plan_emits_api_plan_only_from_indexed_capability_evidence() -> None:
    problem = select_ml_problem(
        usecase_title="Forecast customer monthly spend",
        business_objective="Predict future spend by customer",
        dataset_profiles=_customer_spend_profile(),
        candidate_target="total_spend",
    )
    readiness = assess_feature_readiness(
        problem_type="forecasting",
        dataset_profiles=_customer_spend_profile(),
        target_column="total_spend",
        entity_key="customer_id",
        time_column="month",
    )

    plan = plan_ml_strategy(
        problem_selection=problem,
        feature_readiness=readiness,
        dataset_profiles=_customer_spend_profile(),
        model_full_name="main.ml.customer_spend_forecast",
        capability_evidence=[
            {"entity_id": "openapi:2.1:JobsRunsSubmit"},
            {"entity_id": "docs:mlflow-unity-catalog-models"},
        ],
        api_operations=[
            {
                "operation_id": "openapi:2.1:JobsRunsSubmit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
                "body": {"run_name": "brickvision-ml-training"},
                "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
            },
            {
                "operation_id": "openapi:2.0:StatementExecutionGet",
                "method": "GET",
                "path": "/api/2.0/sql/statements/audit-readback",
                "capability_refs": ["openapi:2.0:StatementExecutionGet"],
            },
        ],
    )

    assert plan["status"] == "ready_for_approval"
    assert plan["api_execution_plan"]["operations"][0]["operation_id"] == "openapi:2.1:JobsRunsSubmit"
    assert "openapi:2.1:JobsRunsSubmit" in plan["api_execution_plan"]["capability_refs"]


def test_strategy_plan_does_not_emit_api_plan_from_unbound_openapi_refs() -> None:
    problem = select_ml_problem(
        usecase_title="Forecast customer monthly spend",
        business_objective="Predict future spend by customer",
        dataset_profiles=_customer_spend_profile(),
        candidate_target="total_spend",
    )
    readiness = assess_feature_readiness(
        problem_type="forecasting",
        dataset_profiles=_customer_spend_profile(),
        target_column="total_spend",
        entity_key="customer_id",
        time_column="month",
    )

    plan = plan_ml_strategy(
        problem_selection=problem,
        feature_readiness=readiness,
        dataset_profiles=_customer_spend_profile(),
        model_full_name="main.ml.customer_spend_forecast",
        capability_evidence=[
            {
                "entity_id": "ext:databricks-jobs-runs-submit",
                "entity_kind": "openapi_operation_ref",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
            }
        ],
        api_operations=[
            {
                "operation_id": "ext:databricks-jobs-runs-submit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
                "capability_refs": ["ext:databricks-jobs-runs-submit"],
            }
        ],
    )

    assert plan["api_execution_plan"] is None
    assert "api_execution_plan" in plan["required_bindings"]


def test_strategy_plan_blocks_when_readiness_fails() -> None:
    problem = select_ml_problem(
        usecase_title="Personalized spend recommendation model",
        business_objective="Recommend next best spend action",
        dataset_profiles=_customer_spend_profile(),
    )
    readiness = assess_feature_readiness(
        problem_type="ranking",
        dataset_profiles=_customer_spend_profile(),
        target_column="total_spend",
        entity_key="customer_id",
    )

    plan = plan_ml_strategy(
        problem_selection=problem,
        feature_readiness=readiness,
        dataset_profiles=_customer_spend_profile(),
        model_full_name="main.ml.spend_recommendation",
    )

    assert plan["status"] == "blocked"
    assert "PROBLEM_SELECTION_NOT_READY" in {
        finding["code"] for finding in plan["findings"]
    }
