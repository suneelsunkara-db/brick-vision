"""Eval scorer registry + base contract.

Per `docs/17-eval-framework.md` §13, scorers run inside
`mlflow.genai.evaluate(...)` and emit `(score: 0..1, reason_codes: [str])`.
The `register_scorer` decorator binds a callable to (skill_id, name); the
emitted skill folders' `scorers.py` use this decorator.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any


@dataclasses.dataclass(frozen=True)
class ScorerResult:
    score: float
    reason_codes: tuple[str, ...] = ()
    details: dict[str, Any] = dataclasses.field(default_factory=dict)


_REGISTRY: dict[tuple[str, str], Callable[..., ScorerResult]] = {}


def register_scorer(*, skill_id: str, name: str):
    """Decorator: register a scorer under `(skill_id, name)`."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[(skill_id, name)] = fn
        return fn

    return deco


def get_scorer(skill_id: str, name: str) -> Callable[..., ScorerResult] | None:
    return _REGISTRY.get((skill_id, name))


def list_scorers() -> dict[tuple[str, str], Callable[..., ScorerResult]]:
    return dict(_REGISTRY)


__all__ = ["ScorerResult", "get_scorer", "list_scorers", "register_scorer"]
