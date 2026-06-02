import base64
import importlib.util
import json
from pathlib import Path


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "delta.pyspark-task-plan" / "skill.py"
    spec = importlib.util.spec_from_file_location("_delta_pyspark_task_plan_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_delta_pyspark_task_plan


def test_pyspark_task_plan_blocks_without_real_driver_uri() -> None:
    result = _runner()(
        transform_id="tx-1",
        transform_code="def transform(spark, inputs):\n    return next(iter(inputs.values()))",
        input_uris=["main.demo.input"],
        output_uri="main.demo.output",
        expected_output_schema={"id": "BIGINT"},
    )

    assert result["status"] == "blocked"
    assert "PYSPARK_DRIVER_URI_REQUIRED" in {finding["code"] for finding in result["findings"]}


def test_pyspark_task_plan_emits_jobs_body_with_encoded_payload() -> None:
    result = _runner()(
        transform_id="tx-1",
        transform_code="def transform(spark, inputs):\n    return next(iter(inputs.values()))",
        input_uris=["main.demo.input"],
        output_uri="main.demo.output",
        expected_output_schema={"id": "BIGINT"},
        pyspark_driver_uri="dbfs:/Volumes/main/demo/artifacts/pyspark_transform_driver.py",
    )

    body = result["job_submit_body"]
    payload = json.loads(
        base64.b64decode(body["tasks"][0]["spark_python_task"]["parameters"][0]).decode("utf-8")
    )
    assert result["status"] == "ready"
    assert body["tasks"][0]["spark_python_task"]["python_file"].endswith("pyspark_transform_driver.py")
    assert payload["transform_id"] == "tx-1"
    assert payload["output_uri"] == "main.demo.output"
