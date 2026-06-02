"""LLM-with-tools skill: skill:uc.catalog-bootstrap-design (auto-generated)."""

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
        # bv:llm-prose:start id=section:role hash=12a68abbc85ce00a
        text="You design a UC catalog bootstrap (catalog + schemas + initial grants) given an organisational intent.",
        # bv:llm-prose:end id=section:role
    ),
    SystemPromptSection(
        id="constraints",
        altitude="medium",
        # bv:llm-prose:start id=section:constraints hash=580eec183e9ce011
        text="Cite evidence claim_ids for every existing entity referenced.\nSurface risk flags.\nNever recommend metastore-admin or account-admin grants.\n",
        # bv:llm-prose:end id=section:constraints
    ),
    SystemPromptSection(
        id="output_schema",
        altitude="low",
        # bv:llm-prose:start id=section:output_schema hash=bca852b356c63f06
        text="Emit `bootstrap_plan` matching the declared output schema, strict mode.",
        # bv:llm-prose:end id=section:output_schema
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:uc.catalog-bootstrap-design",
    version="0.1.0",
    model_role="skill_runtime_premium",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:uc.list_catalogs", "tool:uc.list_schemas", "tool:kg.query_beliefs"],
    behavior_constraints=BehaviorConstraints(**{"must_emit_evidence_chain": true, "must_surface_risk_flags": true, "no_recommend_metastore_admin": true, "no_recommend_account_admin": true}),
    max_turns=8,
    constitutional=["no.write.to.uc", "must.emit.evidence.chain"],
)
# bv:templated:end id=skill
