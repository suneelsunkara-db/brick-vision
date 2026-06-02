"""LLM-with-tools skill: skill:uc.tag-taxonomy-design (auto-generated)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.harness import (
    BehaviorConstraints,
    Skill,
    SystemPromptSection,
)
# bv:templated:end id=imports


# bv:templated:start id=prompt_sections
SYSTEM_PROMPT_SECTIONS: list[SystemPromptSection] = [
    SystemPromptSection(
        id="role",
        altitude="high",
        # bv:llm-prose:start id=section:role hash=7e6ef14efd9cd019
        text="You design a UC tag taxonomy (tag keys + allowed value sets) given an organisational intent.",
        # bv:llm-prose:end id=section:role
    ),
    SystemPromptSection(
        id="constraints",
        altitude="medium",
        # bv:llm-prose:start id=section:constraints hash=06a916a5ccb7cfce
        text="Cite evidence claim_ids for existing tags.\nNever recommend a taxonomy that conflicts with existing widely-applied tags without an explicit migration plan.\n",
        # bv:llm-prose:end id=section:constraints
    ),
    SystemPromptSection(
        id="output_schema",
        altitude="low",
        # bv:llm-prose:start id=section:output_schema hash=19a24c8930c7219d
        text="Emit `taxonomy` matching the declared output schema, strict mode.",
        # bv:llm-prose:end id=section:output_schema
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:uc.tag-taxonomy-design",
    version="0.1.0",
    model_role="skill_runtime_premium",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:uc.list_tags_full", "tool:kg.query_beliefs"],
    behavior_constraints=BehaviorConstraints(**{"must_emit_evidence_chain": true, "must_surface_risk_flags": true}),
    max_turns=8,
    constitutional=["no.write.to.uc", "must.emit.evidence.chain"],
)
# bv:templated:end id=skill
