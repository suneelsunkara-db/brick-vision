"""LLM-with-tools skill: skill:uc.grant-recommend (auto-generated)."""

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
        # bv:llm-prose:start id=section:role hash=5acbe51e4625e4f0
        text="You recommend least-privilege UC grants given an intent and a target principal.",
        # bv:llm-prose:end id=section:role
    ),
    SystemPromptSection(
        id="constraints",
        altitude="medium",
        # bv:llm-prose:start id=section:constraints hash=aee7231eb58edee4
        text="Cite evidence claim_ids for every recommended grant.\nSurface risk flags. Never recommend metastore-admin or account-admin grants.\nNever invent securables that don't exist in retrieved Beliefs.\n",
        # bv:llm-prose:end id=section:constraints
    ),
    SystemPromptSection(
        id="output_schema",
        altitude="low",
        # bv:llm-prose:start id=section:output_schema hash=af32a4ffb8be7a14
        text="Emit `recommendation` matching the declared output JSON schema, strict mode.",
        # bv:llm-prose:end id=section:output_schema
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:uc.grant-recommend",
    version="0.1.0",
    model_role="skill_runtime_premium",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:uc.list_grants_for_securable", "tool:uc.list_grants_for_principal", "tool:uc.resolve_principal", "tool:kg.query_beliefs", "tool:kg.emit_claims"],
    behavior_constraints=BehaviorConstraints(**{"must_emit_evidence_chain": true, "must_surface_risk_flags": true, "no_recommend_account_admin": true, "no_recommend_metastore_admin": true}),
    max_turns=8,
    constitutional=["no.write.to.uc", "must.emit.evidence.chain", "must.surface.risk.flags", "no.recommend.account.admin.grants", "no.recommend.metastore.admin.grants"],
)
# bv:templated:end id=skill
