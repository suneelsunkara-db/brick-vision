"""Violation 5 — module filename ends with ``_stub``.

Triggers ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` (kind=filename).

Even though the class name + function names below are clean, the
filename pattern alone is a violation.
"""

from __future__ import annotations


class TelemetryAggregator:
    """Aggregates telemetry rows for downstream export."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append(self, row: dict[str, object]) -> None:
        self.rows.append(row)
