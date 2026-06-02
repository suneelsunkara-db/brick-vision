from typing import Any

from brickvision_runtime.ml import TrainingSpec
from brickvision_runtime.ml.databricks_training import (
    DatabricksApiPlanBlocked,
    run_databricks_training_job,
)


class _RecordingApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def do(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if path == "/api/2.1/jobs/runs/submit":
            return {"run_id": 123}
        if path == "/api/2.1/jobs/runs/get?run_id=123":
            return {"state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}}
        if path == "/api/2.0/sql/statements/audit-readback":
            return {
                "model_training_run": {
                    "audit_id": "audit-1",
                    "model_id": "main.ml.customer_spend_forecast",
                    "skill_id": "skill:ml.train-evaluate-register",
                    "val_metric_name": "rmse",
                    "val_metric_value": 9.0,
                    "val_metric_floor": 10.0,
                    "val_floor_passed": True,
                    "train_row_count": 80,
                    "validation_row_count": 20,
                    "registered_model_name": "main.ml.customer_spend_forecast",
                    "registered_model_version": 1,
                    "feature_set_hash": "feature-hash",
                }
            }
        raise AssertionError(f"Unexpected API call: {method} {path}")


class _RecordingClient:
    def __init__(self) -> None:
        self.api_client = _RecordingApiClient()


def _spec() -> TrainingSpec:
    return TrainingSpec(
        model_id="main.ml.customer_spend_forecast",
        strategy_plan={
            "status": "ready_for_approval",
            "registry": "Unity Catalog Registered Model",
            "uc_model_name": "main.ml.customer_spend_forecast",
            "problem_type": "forecasting",
            "metric_candidates": ["rmse", "mae"],
        },
        strategy_approval_id="approval-1",
        feature_columns=("txn_count",),
        label_column="total_spend",
        primary_key="customer_id",
        transforms=(),
        val_metric_name="rmse",
        val_metric_floor=10.0,
        split_seed=7,
    )


def _api_plan() -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "capability_refs": [
            "sdk:workspace:jobs.submit",
            "openapi:2.1:JobsRunsSubmit",
            "docs:mlflow-unity-catalog-models",
        ],
        "operations": [
            {
                "operation_id": "openapi:2.1:JobsRunsSubmit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
                "body": {"run_name": "from-skill-plan"},
                "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
                "wait": {"kind": "jobs_run_terminated", "timeout_sec": 30, "poll_sec": 1},
            }
        ],
        "audit_readback": {
            "operation": {
                "operation_id": "openapi:2.0:StatementExecutionGet",
                "method": "GET",
                "path": "/api/2.0/sql/statements/audit-readback",
                "body": {},
                "capability_refs": ["openapi:2.0:StatementExecutionGet"],
            }
        },
    }


def test_adapter_blocks_without_skill_emitted_api_plan() -> None:
    try:
        run_databricks_training_job(
            spec=_spec(),
            rows_uri="main.ml.training_rows",
            skill_id="skill:ml.train-evaluate-register",
            audit_id="audit-1",
            client=_RecordingClient(),
        )
    except DatabricksApiPlanBlocked as exc:
        assert "api_execution_plan" in str(exc)
    else:
        raise AssertionError("adapter ran without a capability-grounded API plan")


def test_adapter_executes_only_declared_capability_plan_operations() -> None:
    client = _RecordingClient()

    run = run_databricks_training_job(
        spec=_spec(),
        rows_uri="main.ml.training_rows",
        skill_id="skill:ml.train-evaluate-register",
        audit_id="audit-1",
        api_execution_plan=_api_plan(),
        client=client,
    )

    assert run.registered_model_name == "main.ml.customer_spend_forecast"
    assert [call[:2] for call in client.api_client.calls] == [
        ("POST", "/api/2.1/jobs/runs/submit"),
        ("GET", "/api/2.1/jobs/runs/get?run_id=123"),
        ("GET", "/api/2.0/sql/statements/audit-readback"),
    ]


def test_adapter_reads_statement_execution_audit_row() -> None:
    class _StatementApiClient(_RecordingApiClient):
        def do(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            self.calls.append((method, path, body))
            if path == "/api/2.1/jobs/runs/submit":
                return {"run_id": 123}
            if path == "/api/2.1/jobs/runs/get?run_id=123":
                return {"state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}}
            if path == "/api/2.0/sql/statements":
                return {
                    "status": {"state": "SUCCEEDED"},
                    "manifest": {
                        "schema": {
                            "columns": [
                                {"name": "audit_id"},
                                {"name": "model_id"},
                                {"name": "skill_id"},
                                {"name": "val_metric_name"},
                                {"name": "val_metric_value"},
                                {"name": "val_metric_floor"},
                                {"name": "val_floor_passed"},
                                {"name": "train_row_count"},
                                {"name": "validation_row_count"},
                                {"name": "registered_model_name"},
                                {"name": "registered_model_version"},
                                {"name": "feature_set_hash"},
                            ]
                        }
                    },
                    "result": {
                        "data_array": [
                            [
                                "audit-1",
                                "main.ml.customer_spend_forecast",
                                "skill:ml.train-evaluate-register",
                                "rmse",
                                "9.0",
                                "10.0",
                                "true",
                                "80",
                                "20",
                                "main.ml.customer_spend_forecast",
                                "1",
                                "feature-hash",
                            ]
                        ]
                    },
                }
            raise AssertionError(f"Unexpected API call: {method} {path}")

    class _StatementClient:
        def __init__(self) -> None:
            self.api_client = _StatementApiClient()

    plan = _api_plan()
    plan["audit_readback"]["operation"] = {
        "operation_id": "openapi:2.0:StatementExecutionExecuteStatement",
        "method": "POST",
        "path": "/api/2.0/sql/statements",
        "body": {"warehouse_id": "wh", "statement": "SELECT * FROM audit"},
        "capability_refs": ["openapi:2.0:StatementExecutionExecuteStatement"],
    }
    run = run_databricks_training_job(
        spec=_spec(),
        rows_uri="main.ml.training_rows",
        skill_id="skill:ml.train-evaluate-register",
        audit_id="audit-1",
        api_execution_plan=plan,
        client=_StatementClient(),
    )

    assert run.registered_model_version == 1
