"""N11: ContextBudget + token estimators.

Per `docs/13-model-routing-and-budget.md` §11.4, budgets are tokens-primary,
USD-advisory. The `ContextBudget` is the typed cap for a single
build-pipeline run; the per-stage breakdown comes from `.env` defaults.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable


@dataclasses.dataclass(frozen=True)
class ContextBudget:
    input_tokens_cap: int
    output_tokens_cap: int
    daily_tokens_cap: int
    cost_alert_usd: float
    design_sketch_input_cap: int = 20_000
    design_sketch_output_cap: int = 4_000
    design_per_section_input_cap: int = 15_000
    design_per_section_output_cap: int = 3_000
    design_max_section_retries: int = 2

    def remaining_input(self, used: int) -> int:
        return max(0, self.input_tokens_cap - used)

    def remaining_output(self, used: int) -> int:
        return max(0, self.output_tokens_cap - used)


def estimate_tokens(text: str) -> int:
    """Cheap heuristic: ~4 chars per token. Production routes through the
    server-side tokenizer per role at audit time; this is the build-time
    predictor used by the budget guard.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def estimate_messages_tokens(messages: Iterable[dict]) -> int:
    return sum(estimate_tokens(m.get("content", "") or "") for m in messages)


def will_overflow(*, used: int, cap: int, projected_add: int) -> bool:
    return used + projected_add > cap


__all__ = [
    "ContextBudget",
    "estimate_messages_tokens",
    "estimate_tokens",
    "will_overflow",
]
