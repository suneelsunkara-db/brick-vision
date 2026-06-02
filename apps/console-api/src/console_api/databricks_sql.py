"""Small Databricks SQL Statement Execution boundary."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import request


def workspace_client() -> Any:
    """Create a Databricks client without host metadata auto-discovery."""

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]

    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if host and token:
        return WorkspaceClient(host=host, token=token)
    return WorkspaceClient()


def execute_sql_statement(statement: str) -> None:
    if _has_rest_credentials():
        response = _execute_statement_rest(statement=statement, row_limit=None)
        state = str(((response.get("status") or {}).get("state") or "")).upper()
        if state != "SUCCEEDED":
            err = (response.get("status") or {}).get("error") or {}
            msg = err.get("message") or "(no error message)"
            raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")
        return

    from databricks.sdk.service.sql import StatementState  # type: ignore[import-not-found]

    warehouse_id = resolve_warehouse_id()
    client = workspace_client()
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")


def query_sql_statement_rows(statement: str) -> list[list[Any]]:
    if _has_rest_credentials():
        response = _execute_statement_rest(statement=statement, row_limit=100)
        state = str(((response.get("status") or {}).get("state") or "")).upper()
        if state != "SUCCEEDED":
            err = (response.get("status") or {}).get("error") or {}
            msg = err.get("message") or "(no error message)"
            raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        data = result.get("data_array")
        return [list(row) for row in data] if isinstance(data, list) else []

    from databricks.sdk.service.sql import StatementState  # type: ignore[import-not-found]

    warehouse_id = resolve_warehouse_id()
    client = workspace_client()
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        row_limit=100,
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")
    if response.result and response.result.data_array:
        return [list(row) for row in response.result.data_array]
    return []


def resolve_warehouse_id() -> str:
    warehouse_id = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_WAREHOUSE_ID", "").strip()
    )
    if not warehouse_id:
        raise RuntimeError(
            "DATABRICKS_WAREHOUSE_ID is required to execute workspace builds"
        )
    return warehouse_id


def _has_rest_credentials() -> bool:
    warehouse_id = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_WAREHOUSE_ID", "").strip()
    )
    return bool(
        os.environ.get("DATABRICKS_HOST", "").strip()
        and os.environ.get("DATABRICKS_TOKEN", "").strip()
        and warehouse_id
    )


def _execute_statement_rest(*, statement: str, row_limit: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "statement": statement,
        "warehouse_id": resolve_warehouse_id(),
        "wait_timeout": "50s",
    }
    if row_limit is not None:
        body["row_limit"] = row_limit
    response = _databricks_request("POST", "/api/2.0/sql/statements", body=body)
    if not isinstance(response, dict):
        raise RuntimeError(f"Statement Execution returned unexpected response: {response}")
    statement_id = str(response.get("statement_id") or "")
    if not statement_id:
        return response
    deadline = time.monotonic() + int(os.environ.get("DATABRICKS_SQL_POLL_TIMEOUT_SECONDS", "60"))
    current = response
    while time.monotonic() < deadline:
        state = str(((current.get("status") or {}).get("state") or "")).upper()
        if state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}:
            return current
        time.sleep(2)
        latest = _databricks_request("GET", f"/api/2.0/sql/statements/{statement_id}")
        if not isinstance(latest, dict):
            raise RuntimeError(f"Statement Execution polling returned unexpected response: {latest}")
        current = latest
    raise TimeoutError(f"Timed out waiting for Statement Execution {statement_id}: {current.get('status')}")


def _databricks_request(method: str, path: str, body: dict[str, Any] | None = None) -> object:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(
        f"{host}{path}",
        data=payload,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=60) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def qualified_uc_name(object_name: str) -> str:
    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    return ".".join(
        (
            quote_identifier(catalog),
            quote_identifier(schema),
            quote_identifier(object_name),
        )
    )


def quote_identifier(value: str) -> str:
    if not value:
        raise ValueError("empty SQL identifier")
    return "`" + value.replace("`", "``") + "`"


def sql_string_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

