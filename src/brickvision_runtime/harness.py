"""Runtime skill contract primitives.

These classes are intentionally small and dependency-free. Skill modules import
them to declare their contract; execution code wires concrete tool adapters at
the boundary.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class SystemPromptSection:
    id: str
    altitude: str
    text: str


@dataclasses.dataclass(frozen=True)
class BehaviorConstraints:
    must_emit_evidence_chain: bool = False
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __init__(self, must_emit_evidence_chain: bool = False, **kwargs: Any) -> None:
        object.__setattr__(self, "must_emit_evidence_chain", must_emit_evidence_chain)
        extra = dict(kwargs.pop("extra", {}) or {})
        extra.update(kwargs)
        object.__setattr__(self, "extra", extra)


@dataclasses.dataclass(frozen=True)
class Skill:
    id: str
    version: str
    mode: str
    model_role: str | None = None
    system_prompt_sections: tuple[SystemPromptSection, ...] = ()
    tool_pool: tuple[str, ...] = ()
    behavior_constraints: BehaviorConstraints | None = None
    max_turns: int | None = None
    constitutional: tuple[str, ...] = ()
    dag: Any | None = None

    @classmethod
    def llm_with_tools(
        cls,
        *,
        id: str,
        version: str,
        model_role: str,
        system_prompt_sections: list[SystemPromptSection] | tuple[SystemPromptSection, ...],
        tool_pool: list[str] | tuple[str, ...],
        behavior_constraints: BehaviorConstraints,
        max_turns: int,
        constitutional: list[str] | tuple[str, ...] = (),
    ) -> "Skill":
        return cls(
            id=id,
            version=version,
            mode="llm_with_tools",
            model_role=model_role,
            system_prompt_sections=tuple(system_prompt_sections),
            tool_pool=tuple(tool_pool),
            behavior_constraints=behavior_constraints,
            max_turns=max_turns,
            constitutional=tuple(constitutional),
        )

    @classmethod
    def mechanical(
        cls,
        *,
        id: str,
        version: str,
        dag: Any,
        constitutional: list[str] | tuple[str, ...] = (),
    ) -> "Skill":
        return cls(
            id=id,
            version=version,
            mode="mechanical",
            dag=dag,
            constitutional=tuple(constitutional),
        )


__all__ = ["BehaviorConstraints", "Skill", "SystemPromptSection"]
