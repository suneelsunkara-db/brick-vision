"""Violation 2 — class with the ``Mock`` prefix.

Triggers ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` (kind=class).
"""

from __future__ import annotations


class MockWorkspaceClient:
    """Stand-in for ``databricks.sdk.WorkspaceClient`` used during tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute_statement(self, statement: str, **kwargs: object) -> None:
        self.calls.append((statement, dict(kwargs)))
