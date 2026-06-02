"""Violation 3 — class with the ``Dummy`` prefix.

Triggers ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` (kind=class).
"""

from __future__ import annotations


class DummyTracer:
    """No-op tracer used when the real tracing stack is unavailable."""

    def start_span(self, name: str) -> None:
        return None

    def end_span(self, name: str) -> None:
        return None
