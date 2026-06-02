import importlib.util
from pathlib import Path


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.training-backend-probe" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_training_backend_probe_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_training_backend_probe


def test_backend_probe_plans_non_training_probe_job() -> None:
    result = _runner()(
        runtime_surface="serverless_jobs",
        probe_driver_uri="dbfs:/Volumes/main/brickvision/artifacts/ml/backend_probe_driver.py",
        probe_output_table="main.brickvision.backend_probe_results",
        probe_id="probe-1",
    )

    body = result["job_submit_body"]
    assert result["status"] == "probe_required"
    assert body["tasks"][0]["task_key"] == "probe_ml_backends"
    assert body["tasks"][0]["spark_python_task"]["python_file"].endswith("backend_probe_driver.py")
    assert "--probe-output-table" in body["tasks"][0]["spark_python_task"]["parameters"]


def test_backend_probe_normalizes_observed_runtime_evidence() -> None:
    result = _runner()(
        runtime_surface="serverless_jobs",
        probe_result={
            "probe_id": "probe-1",
            "runtime_surface": "serverless_jobs",
            "databricks_automl_available": False,
            "spark_ml_allowed": True,
            "mlflow_uc_registry_available": True,
            "mosaic_ai_available": False,
            "substrate_json": '{"python_imports":{"sklearn":true},"data_movement":{"to_pandas_available":true}}',
            "errors_json": {"databricks_automl": "ImportError"},
        },
    )

    assert result["status"] == "ready"
    assert result["job_submit_body"] is None
    assert result["runtime_evidence"]["databricks_automl_available"] is False
    assert result["runtime_evidence"]["spark_ml_allowed"] is True
    assert result["runtime_evidence"]["substrate"]["python_imports"]["sklearn"] is True
