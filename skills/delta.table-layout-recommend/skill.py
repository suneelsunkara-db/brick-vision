"""LLM-with-tools skill: skill:delta.table-layout-recommend (auto-generated)."""

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
        # bv:llm-prose:start id=section:role hash=b6f85d482719b727
        text="You recommend Delta table layout (partition keys, liquid clustering, predictive optimization, tuning) given the table's storage profile and access patterns.",
        # bv:llm-prose:end id=section:role
    ),
    SystemPromptSection(
        id="constraints",
        altitude="medium",
        # bv:llm-prose:start id=section:constraints hash=bdec57449aa33227
        text="Cite evidence claim_ids for every recommendation.\nNever recommend partitioning a table < 1 GB.\nNever propose breaking changes (table re-create) without an explicit migration plan.\n",
        # bv:llm-prose:end id=section:constraints
    ),
    SystemPromptSection(
        id="output_schema",
        altitude="low",
        # bv:llm-prose:start id=section:output_schema hash=0bdb37825b632054
        text="Emit `recommendation` matching the declared output schema, strict mode.",
        # bv:llm-prose:end id=section:output_schema
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:delta.table-layout-recommend",
    version="0.1.0",
    model_role="skill_runtime_premium",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:delta.describe_extended", "tool:delta.describe_history", "tool:kg.query_beliefs"],
    behavior_constraints=BehaviorConstraints(**{"must_emit_evidence_chain": true, "must_surface_risk_flags": true}),
    max_turns=6,
    constitutional=["no.write.to.uc", "must.emit.evidence.chain", "must.surface.risk.flags"],
)
# bv:templated:end id=skill
