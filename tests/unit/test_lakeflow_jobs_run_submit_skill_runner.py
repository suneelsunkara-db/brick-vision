import importlib.util
from pathlib import Path
from typing import Any


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "lakeflow.jobs-run-submit" / "skill.py"
    spec = importlib.util.spec_from_file_location("_lakeflow_jobs_run_submit_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_lakeflow_jobs_run_submit


def _job_body() -> dict[str, Any]:
    return {
        "run_name": "brickvision-ml-training",
        "tasks": [
            {
                "task_key": "train",
                "spark_python_task": {
                    "python_file": "dbfs:/brickvision/ml/train.py",
                    "parameters": ["--rows-uri", "main.ml.training_rows"],
                },
            }
        ],
    }


def test_jobs_run_submit_blocks_without_body() -> None:
    result = _runner()(
        capability_evidence=[{"entity_id": "openapi:2.1:JobsRunsSubmit"}],
    )

    assert result["status"] == "blocked"
    assert result["api_operation"] is None
    assert "JOB_SUBMIT_BODY_REQUIRED" in {finding["code"] for finding in result["findings"]}


def test_jobs_run_submit_returns_grounded_api_operation() -> None:
    result = _runner()(
        capability_evidence=[{"entity_id": "openapi:2.1:JobsRunsSubmit"}],
        job_submit_body=_job_body(),
    )

    assert result["status"] == "ready"
    assert result["api_operation"]["path"] == "/api/2.1/jobs/runs/submit"
    assert result["api_operation"]["body"]["run_name"] == "brickvision-ml-training"
