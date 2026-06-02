"""Mechanical DAG contract used by skill declarations."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class DAGStep:
    id: str
    tool: str = ""
    inputs: dict[str, Any] = dataclasses.field(default_factory=dict)
    kind: str = "tool"
    function: str = ""
    for_each: str = ""


@dataclasses.dataclass(frozen=True)
class DAG:
    name: str
    steps: tuple[DAGStep, ...] = ()

    def step(
        self,
        *,
        id: str,
        tool: str = "",
        kind: str = "tool",
        function: str = "",
        for_each: str = "",
        inputs: dict[str, Any] | None = None,
    ) -> "DAG":
        return DAG(
            name=self.name,
            steps=(
                *self.steps,
                DAGStep(
                    id=id,
                    tool=tool,
                    inputs=dict(inputs or {}),
                    kind=kind,
                    function=function,
                    for_each=for_each,
                ),
            ),
        )


__all__ = ["DAG", "DAGStep"]
