import importlib.util
from pathlib import Path


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "databricks.statement-execute" / "skill.py"
    spec = importlib.util.spec_from_file_location("_databricks_statement_execute_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_databricks_statement_execute


def test_statement_execute_blocks_without_bound_statement() -> None:
    result = _runner()(
        capability_evidence=[{"entity_id": "openapi:2.0:StatementExecutionExecuteStatement"}],
        warehouse_id="warehouse-1",
    )

    assert result["status"] == "blocked"
    assert "SQL_STATEMENT_REQUIRED" in {finding["code"] for finding in result["findings"]}


def test_statement_execute_returns_grounded_api_operation() -> None:
    result = _runner()(
        capability_evidence=[{"entity_id": "openapi:2.0:StatementExecutionExecuteStatement"}],
        statement="CREATE OR REPLACE TABLE main.demo.out AS SELECT 1 AS id",
        warehouse_id="warehouse-1",
    )

    operation = result["api_operation"]
    assert result["status"] == "ready"
    assert operation["method"] == "POST"
    assert operation["path"] == "/api/2.0/sql/statements"
    assert operation["body"]["warehouse_id"] == "warehouse-1"
    assert operation["body"]["statement"].startswith("CREATE OR REPLACE TABLE")
    assert operation["wait"]["kind"] == "sql_statement_succeeded"
