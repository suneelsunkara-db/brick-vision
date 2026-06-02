import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "console-api" / "src"))
sys.path.insert(0, str(ROOT / "src"))

from console_api import skill_execution_service as service  # noqa: E402


def test_openapi_provenance_becomes_capability_evidence_not_execution() -> None:
    refs = service._openapi_refs_from_provenance(
        entity_id="ext:databricks-jobs-runs-submit",
        provenance={
            "contributing_chunks": [
                {
                    "source_id": "openapi",
                    "source_url": "POST /api/2.1/jobs/runs/submit",
                }
            ]
        },
    )

    assert refs == [
        {
            "entity_id": "ext:databricks-jobs-runs-submit",
            "entity_kind": "openapi_operation_ref",
            "method": "POST",
            "path": "/api/2.1/jobs/runs/submit",
        }
    ]


def test_ml_training_gate_blocks_without_api_execution_plan() -> None:
    gate = service._ml_training_gate(
        strategy={"status": "ready_for_approval"},
        inputs={
            "model_full_name": "main.ml.customer_spend",
            "strategy_approval_id": "approval-1",
            "feature_columns": ["a"],
            "label_column": "label",
            "primary_key": "id",
            "rows_uri": "uc:main.ml.rows",
            "val_metric_name": "rmse",
            "val_metric_floor": 10,
            "split_seed": 42,
        },
    )

    assert gate["status"] == "blocked"
    assert "api_execution_plan" in gate["missing"]
