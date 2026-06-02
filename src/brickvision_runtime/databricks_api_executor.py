"""Shared thin executor for capability-grounded Databricks API operations."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from typing import Any
from urllib import request


@dataclasses.dataclass(frozen=True)
class DatabricksApiOperation:
    operation_id: str
    method: str
    path: str
    body: dict[str, Any]
    capability_refs: tuple[str, ...]
    wait: dict[str, Any]


class DatabricksApiExecutionError(RuntimeError):
    """Raised when an API operation or declared wait condition fails."""


def workspace_client() -> Any:
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if host and token:
        return _RestWorkspaceClient(host=host, token=token)

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]

    return WorkspaceClient()


class _RestWorkspaceClient:
    def __init__(self, *, host: str, token: str) -> None:
        self.api_client = _RestApiClient(host=host, token=token)


class _RestApiClient:
    def __init__(self, *, host: str, token: str) -> None:
        self._host = host.rstrip("/")
        self._token = token

    def do(self, method: str, path: str, body: dict[str, Any] | None = None) -> object:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = request.Request(
            f"{self._host}{path}",
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        with request.urlopen(req, timeout=60) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def operation_from_dict(raw: object) -> DatabricksApiOperation:
    if not isinstance(raw, dict):
        raise DatabricksApiExecutionError("Databricks API operation must be an object.")
    operation = DatabricksApiOperation(
        operation_id=str(raw.get("operation_id") or ""),
        method=str(raw.get("method") or "").upper(),
        path=str(raw.get("path") or ""),
        body=dict(raw.get("body") or {}),
        capability_refs=_string_tuple(raw.get("capability_refs")),
        wait=dict(raw.get("wait") or {}),
    )
    if (
        not operation.operation_id
        or not operation.path
        or operation.method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    ):
        raise DatabricksApiExecutionError("Databricks API operation needs operation_id, method, and path.")
    if operation.method in {"POST", "PUT", "PATCH"} and not operation.body:
        raise DatabricksApiExecutionError(f"Operation {operation.operation_id} must include a bound request body.")
    if not operation.capability_refs:
        raise DatabricksApiExecutionError(f"Operation {operation.operation_id} must cite capability_refs.")
    return operation


def execute_operation(*, client: Any, operation: DatabricksApiOperation | dict[str, Any]) -> object:
    parsed = operation_from_dict(operation) if isinstance(operation, dict) else operation
    response = client.api_client.do(parsed.method, parsed.path, body=parsed.body)
    return wait_for_operation(client=client, operation=parsed, response=response)


def wait_for_operation(*, client: Any, operation: DatabricksApiOperation, response: object) -> object:
    if not operation.wait:
        return response
    wait_kind = str(operation.wait.get("kind") or "")
    if wait_kind == "jobs_run_terminated":
        if not isinstance(response, dict) or not response.get("run_id"):
            raise DatabricksApiExecutionError(f"Jobs submit operation did not return run_id: {response}")
        return wait_for_job_run(
            client=client,
            run_id=int(response["run_id"]),
            timeout_sec=int(operation.wait.get("timeout_sec") or 3600),
            poll_sec=int(operation.wait.get("poll_sec") or 15),
        )
    if wait_kind == "sql_statement_succeeded":
        if not isinstance(response, dict):
            raise DatabricksApiExecutionError(f"Statement operation returned unexpected response: {response}")
        return wait_for_statement(
            client=client,
            response=response,
            timeout_sec=int(operation.wait.get("timeout_sec") or 1800),
            poll_sec=int(operation.wait.get("poll_sec") or 5),
        )
    raise DatabricksApiExecutionError(f"Unsupported Databricks API wait kind: {wait_kind}")


def wait_for_job_run(*, client: Any, run_id: int, timeout_sec: int, poll_sec: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last_response: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.api_client.do("GET", f"/api/2.1/jobs/runs/get?run_id={run_id}")
        if not isinstance(response, dict):
            raise DatabricksApiExecutionError(f"Jobs runs/get returned unexpected response: {response}")
        last_response = response
        state = response.get("state") if isinstance(response.get("state"), dict) else {}
        life_cycle = str(state.get("life_cycle_state") or "")
        result_state = str(state.get("result_state") or "")
        if life_cycle == "TERMINATED":
            if result_state == "SUCCESS":
                return response
            raise DatabricksApiExecutionError(
                f"Databricks job run {run_id} finished with result_state={result_state}: {state}"
            )
        if life_cycle in {"INTERNAL_ERROR", "SKIPPED"}:
            raise DatabricksApiExecutionError(f"Databricks job run {run_id} failed before termination: {state}")
        time.sleep(max(1, poll_sec))
    raise TimeoutError(f"Timed out waiting for Databricks job run {run_id}: {last_response.get('state')}")


def wait_for_statement(
    *,
    client: Any,
    response: dict[str, Any],
    timeout_sec: int,
    poll_sec: int,
) -> dict[str, Any]:
    current = response
    statement_id = str(current.get("statement_id") or "")
    if not statement_id:
        return current
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        status = current.get("status") if isinstance(current.get("status"), dict) else {}
        state = str(status.get("state") or "").upper()
        if state == "SUCCEEDED":
            return current
        if state in {"FAILED", "CANCELED", "CLOSED"}:
            raise DatabricksApiExecutionError(f"Statement Execution {statement_id} returned state={state}: {status}")
        latest = client.api_client.do("GET", f"/api/2.0/sql/statements/{statement_id}")
        if not isinstance(latest, dict):
            raise DatabricksApiExecutionError(f"Statement Execution polling returned unexpected response: {latest}")
        current = latest
        latest_status = current.get("status") if isinstance(current.get("status"), dict) else {}
        latest_state = str(latest_status.get("state") or "").upper()
        if latest_state == "SUCCEEDED":
            return current
        if latest_state in {"FAILED", "CANCELED", "CLOSED"}:
            raise DatabricksApiExecutionError(
                f"Statement Execution {statement_id} returned state={latest_state}: {latest_status}"
            )
        time.sleep(max(1, poll_sec))
    raise TimeoutError(f"Timed out waiting for Statement Execution {statement_id}: {current.get('status')}")


def statement_row(response: dict[str, Any]) -> dict[str, Any] | None:
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    data = result.get("data_array")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, list):
        return None
    if len(first) == 1:
        import json

        try:
            parsed = json.loads(str(first[0]))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    columns = statement_columns(response)
    if columns and len(columns) == len(first):
        return {name: value for name, value in zip(columns, first, strict=True)}
    return None


def statement_columns(response: dict[str, Any]) -> list[str]:
    schema = ((response.get("manifest") or {}).get("schema") or {})
    raw_columns = schema.get("columns")
    if not isinstance(raw_columns, list):
        return []
    columns: list[str] = []
    for column in raw_columns:
        if isinstance(column, dict) and column.get("name"):
            columns.append(str(column["name"]))
    return columns


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


__all__ = [
    "DatabricksApiExecutionError",
    "DatabricksApiOperation",
    "execute_operation",
    "operation_from_dict",
    "statement_columns",
    "statement_row",
    "wait_for_job_run",
    "wait_for_operation",
    "wait_for_statement",
    "workspace_client",
]
