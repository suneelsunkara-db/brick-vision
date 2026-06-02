"""Violation 4 — function with the ``fake_`` prefix.

Triggers ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` (kind=function).
"""

from __future__ import annotations

from collections.abc import Sequence


def fake_runner(prompt: str, tools: Sequence[object]) -> str:
    """Return a synthetic response without invoking the real LLM."""

    return f"FAKE-RESPONSE: prompt={prompt[:32]!r} tools={len(tools)}"
