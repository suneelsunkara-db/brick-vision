from brickvision_runtime.databricks_api_executor import (
    execute_operation,
    statement_row,
    wait_for_job_run,
    wait_for_statement,
)


class _ApiClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def do(self, method: str, path: str, body: dict | None = None):
        self.calls.append((method, path, body))
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)


class _Client:
    def __init__(self, responses: list[dict]):
        self.api_client = _ApiClient(responses)


def test_execute_operation_waits_for_jobs_run_success() -> None:
    client = _Client(
        [
            {"run_id": 123},
            {"run_id": 123, "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}},
        ]
    )

    response = execute_operation(
        client=client,
        operation={
            "operation_id": "openapi:2.1:JobsRunsSubmit",
            "method": "POST",
            "path": "/api/2.1/jobs/runs/submit",
            "body": {"tasks": []},
            "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
            "wait": {"kind": "jobs_run_terminated", "timeout_sec": 1, "poll_sec": 1},
        },
    )

    assert response["state"]["result_state"] == "SUCCESS"
    assert client.api_client.calls[0][1] == "/api/2.1/jobs/runs/submit"
    assert client.api_client.calls[1][1] == "/api/2.1/jobs/runs/get?run_id=123"


def test_wait_for_statement_returns_succeeded_response() -> None:
    client = _Client(
        [
            {
                "statement_id": "stmt-1",
                "status": {"state": "SUCCEEDED"},
                "result": {"data_array": [[1]]},
            }
        ]
    )

    response = wait_for_statement(
        client=client,
        response={"statement_id": "stmt-1", "status": {"state": "RUNNING"}},
        timeout_sec=1,
        poll_sec=1,
    )

    assert response["status"]["state"] == "SUCCEEDED"


def test_statement_row_parses_columnar_result() -> None:
    assert statement_row(
        {
            "manifest": {"schema": {"columns": [{"name": "audit_id"}, {"name": "row_count"}]}},
            "result": {"data_array": [["audit-1", 5]]},
        }
    ) == {"audit_id": "audit-1", "row_count": 5}


def test_wait_for_job_run_returns_terminal_success_state() -> None:
    client = _Client(
        [
            {"run_id": 99, "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}},
        ]
    )

    response = wait_for_job_run(client=client, run_id=99, timeout_sec=1, poll_sec=1)

    assert response["run_id"] == 99
