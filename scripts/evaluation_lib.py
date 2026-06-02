"""Shared helpers for BrickVision evaluation operator scripts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import request

RESERVED_EXPECTATION_KEYS = {
    "expected_facts",
    "expected_response",
    "guidelines",
    "expected_retrieved_context",
}
SOURCE_KINDS = {"human", "document", "trace", "synthetic"}


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} record must be an object")
            validate_record(record, path=path, line_number=line_number)
            records.append(record)
    return records


def validate_record(record: dict[str, Any], *, path: Path, line_number: int) -> None:
    inputs = record.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError(f"{path}:{line_number} inputs must be a non-empty object")
    expectations = record.get("expectations")
    if expectations is not None and not isinstance(expectations, dict):
        raise ValueError(f"{path}:{line_number} expectations must be an object")
    if isinstance(expectations, dict):
        unknown_reserved = set(expectations) & RESERVED_EXPECTATION_KEYS
        if unknown_reserved and not all(expectations.get(key) for key in unknown_reserved):
            raise ValueError(f"{path}:{line_number} reserved expectation keys cannot be empty")
    source = record.get("source")
    if source is not None:
        source_object = object_or_empty(source)
        source_kinds = set(source_object)
        if len(source_kinds) != 1 or not source_kinds <= SOURCE_KINDS:
            raise ValueError(
                f"{path}:{line_number} source must contain one of {sorted(SOURCE_KINDS)}"
            )
    tags = record.get("tags")
    if tags is not None and not isinstance(tags, dict):
        raise ValueError(f"{path}:{line_number} tags must be an object")


def get_or_create_dataset(
    *,
    name: str,
    experiment_id: str,
    tags: dict[str, Any],
) -> Any:
    del tags  # The Databricks MLflow dataset API does not accept tags on create.
    import mlflow
    from mlflow.genai import datasets as mlflow_datasets

    configure_mlflow_tracking(mlflow)
    try:
        return mlflow_datasets.get_dataset(name=name)
    except Exception:
        kwargs: dict[str, Any] = {"name": name}
        if experiment_id:
            kwargs["experiment_id"] = experiment_id
        return mlflow_datasets.create_dataset(**kwargs)


def configure_mlflow_tracking(mlflow_module: Any) -> None:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if tracking_uri:
        mlflow_module.set_tracking_uri(tracking_uri)
    elif os.environ.get("DATABRICKS_HOST", "").strip():
        mlflow_module.set_tracking_uri("databricks")


def mlflow_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mlflow_record(record) for record in records]


def mlflow_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    source = object_or_empty(record.get("source"))
    if source:
        kind = next(iter(source))
        data = source.get(kind)
        normalized["source"] = {
            "source_type": kind.upper(),
            "source_data": data if isinstance(data, dict) else {"value": data},
        }
        tags = dict(object_or_empty(record.get("tags")))
        tags["brickvision_source_json"] = json.dumps(source, sort_keys=True)
        normalized["tags"] = tags
    return normalized


def register_dataset(
    *,
    dataset_id: str,
    name: str,
    workflow: str,
    uc_table_name: str,
    experiment_id: str,
    description: str,
    quality_gates: dict[str, Any],
    tags: dict[str, Any],
    source_kinds: list[str],
) -> None:
    execute_sql(
        f"""
        CREATE TABLE IF NOT EXISTS {qualified_uc_name("evaluation_datasets")} (
          dataset_id STRING NOT NULL,
          name STRING NOT NULL,
          workflow STRING NOT NULL,
          uc_table_name STRING NOT NULL,
          mlflow_experiment_id STRING,
          description STRING,
          quality_gates_json STRING,
          tags_json STRING,
          source_kinds_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL,
          updated_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'evaluation_datasets')
        """
    )
    now_ms = int(time.time() * 1000)
    created_by = os.environ.get("USER", "unknown")
    execute_sql(
        f"""
        DELETE FROM {qualified_uc_name("evaluation_datasets")}
        WHERE dataset_id = {sql_string_literal(dataset_id)}
           OR name = {sql_string_literal(name)}
        """
    )
    execute_sql(
        f"""
        INSERT INTO {qualified_uc_name("evaluation_datasets")} (
          dataset_id,
          name,
          workflow,
          uc_table_name,
          mlflow_experiment_id,
          description,
          quality_gates_json,
          tags_json,
          source_kinds_json,
          created_by,
          created_at_ms,
          updated_at_ms
        )
        VALUES (
          {sql_string_literal(dataset_id)},
          {sql_string_literal(name)},
          {sql_string_literal(workflow)},
          {sql_string_literal(uc_table_name)},
          {sql_string_literal(experiment_id)},
          {sql_string_literal(description)},
          {sql_string_literal(json.dumps(quality_gates, sort_keys=True))},
          {sql_string_literal(json.dumps(tags, sort_keys=True))},
          {sql_string_literal(json.dumps(source_kinds, sort_keys=True))},
          {sql_string_literal(created_by)},
          {now_ms},
          {now_ms}
        )
        """
    )


def execute_sql(statement: str) -> None:
    try:
        from databricks.sdk.service.sql import StatementState
    except ModuleNotFoundError as exc:
        if exc.name != "databricks":
            raise
        statement_execution_rest(statement=statement)
        return

    client = workspace_client()
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id(),
        wait_timeout="50s",
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")


def query_sql(statement: str) -> list[list[Any]]:
    try:
        from databricks.sdk.service.sql import StatementState
    except ModuleNotFoundError as exc:
        if exc.name != "databricks":
            raise
        return statement_execution_rest(statement=statement)

    client = workspace_client()
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id(),
        wait_timeout="50s",
        row_limit=1000,
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")
    if response.result and response.result.data_array:
        return [list(row) for row in response.result.data_array]
    return []


def statement_execution_rest(*, statement: str) -> list[list[Any]]:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_TOKEN are required for REST fallback")

    response = databricks_rest_request(
        f"{host}/api/2.0/sql/statements/",
        method="POST",
        token=token,
        payload={
            "statement": statement,
            "warehouse_id": warehouse_id(),
            "wait_timeout": "30s",
            "disposition": "INLINE",
            "row_limit": 1000,
        },
    )
    statement_id = str(response.get("statement_id") or "")
    state = str((response.get("status") or {}).get("state") or "")
    for _ in range(20):
        if state not in {"PENDING", "RUNNING"}:
            break
        time.sleep(2)
        response = databricks_rest_request(
            f"{host}/api/2.0/sql/statements/{statement_id}",
            method="GET",
            token=token,
        )
        state = str((response.get("status") or {}).get("state") or "")
    if state != "SUCCEEDED":
        error = (response.get("status") or {}).get("error") or {}
        raise RuntimeError(str(error.get("message") or error or f"statement ended in {state}"))
    return [list(row) for row in (response.get("result") or {}).get("data_array") or []]


def databricks_rest_request(
    url: str,
    *,
    method: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def workspace_client() -> Any:
    from databricks.sdk import WorkspaceClient

    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if host and token:
        return WorkspaceClient(host=host, token=token)
    return WorkspaceClient()


def warehouse_id() -> str:
    value = (
        os.environ.get("BV_EVALUATION_WAREHOUSE_ID", "").strip()
        or os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_WAREHOUSE_ID", "").strip()
    )
    if not value:
        raise RuntimeError("BV_EVALUATION_WAREHOUSE_ID or DATABRICKS_WAREHOUSE_ID is required")
    return value


def qualified_uc_name(object_name: str) -> str:
    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    return ".".join((quote_identifier(catalog), quote_identifier(schema), quote_identifier(object_name)))


def quote_identifier(value: str) -> str:
    if not value:
        raise ValueError("empty SQL identifier")
    return "`" + value.replace("`", "``") + "`"


def sql_string_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def required_string(spec: dict[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"dataset {key!r} must be a non-empty string")
    return value.strip()


def object_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def record_source_kinds(records: list[dict[str, Any]]) -> set[str]:
    kinds: set[str] = set()
    for record in records:
        source = record.get("source")
        if isinstance(source, dict):
            kinds.update(str(key) for key in source)
    return kinds


def stable_dataset_id(name: str) -> str:
    return "mlflow-dataset-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def render_env(value: str) -> str:
    rendered = value
    defaults = {
        "BV_CATALOG": "brickvision",
        "BV_SCHEMA": "brickvision",
        "BV_MLFLOW_EVALUATION_EXPERIMENT_ID": "",
    }
    for key, default in defaults.items():
        rendered = rendered.replace("${" + key + "}", os.environ.get(key, default))
    return rendered
