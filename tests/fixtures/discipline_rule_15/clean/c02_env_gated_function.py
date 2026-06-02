"""Clean 2 — env-gated function on the production code path.

Demonstrates the ``BV_FAKE_LLM``-gated branch *inside* a production
function. The function name doesn't carry a forbidden prefix; the
fake/real distinction lives in the body, not the name.
"""

from __future__ import annotations

import os
from collections.abc import Sequence


def run_sub_agent(prompt: str, tools: Sequence[object]) -> str:
    """Dispatch a sub-agent through the OpenAI Agents SDK runner."""

    if os.environ.get("BV_FAKE_LLM", "false").lower() == "true":
        return _load_canned_response(prompt=prompt, tools=tools)
    return _dispatch_real_runner(prompt=prompt, tools=tools)


def _dispatch_real_runner(*, prompt: str, tools: Sequence[object]) -> str:
    raise NotImplementedError("wired to agents.Runner.run in production")


def _load_canned_response(*, prompt: str, tools: Sequence[object]) -> str:
    raise NotImplementedError(
        "wired to tests/fixtures/coordinator/canned_responses.json in production"
    )
