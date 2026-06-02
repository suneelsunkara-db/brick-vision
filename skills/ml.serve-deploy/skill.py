"""LLM-backed Layer-0 skill: ``skill:ml.serve-deploy`` (N141, refactored N151)."""

from __future__ import annotations

# bv:templated:start id=imports
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from brickvision_runtime.harness import (
    BehaviorConstraints,
    Skill,
    SystemPromptSection,
)
from brickvision_runtime.ml import (
    DeployFn,
    DeploySpec,
    ModelDeploymentRun,
    deploy_endpoint,
)
from brickvision_runtime.ml.serving_alias_aware import (
    AliasAwareDeployRun,
    AliasAwareDeploySpec,
    AliasResolverFn,
    DescribeEndpointFn,
    ServedEntity,
    deploy_endpoint_alias_aware,
)
from brickvision_runtime.ml.serving_alias_aware import DeployFn as AliasAwareDeployFn
# bv:templated:end id=imports


# bv:templated:start id=prompt_sections
SYSTEM_PROMPT_SECTIONS: list[SystemPromptSection] = [
    SystemPromptSection(
        id="role",
        altitude="high",
        text=(
            "You produce a closed deploy recipe: workload_size, "
            "scale_to_zero_enabled, served_model_name."
        ),
    ),
    SystemPromptSection(
        id="schema",
        altitude="high",
        text=(
            "Return JSON {workload_size: 'Small' | 'Medium' | 'Large', "
            "scale_to_zero_enabled: bool, served_model_name: string}. "
            "served_model_name MUST be the alias-driven UC reference, "
            "e.g. 'catalog.schema.model@alias'."
        ),
    ),
    SystemPromptSection(
        id="discipline",
        altitude="medium",
        text=(
            "If scale_to_zero is unsafe (e.g. low-latency endpoint), "
            "explicitly return scale_to_zero_enabled=false. Never "
            "deploy without an alias — emit a Question instead."
        ),
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:ml.serve-deploy",
    version="0.1.0",
    model_role="serve_deploy",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:ml.deploy_endpoint", "tool:hitl.request_approval"],
    behavior_constraints=BehaviorConstraints(
        must_emit_evidence_chain=True,
        extra={
            "must_require_uc_alias": True,
            "must_require_hitl_approval": True,
            "must_emit_model_deployment_run_audit_row": True,
        },
    ),
    max_turns=3,
    constitutional=(
        "deploy.requires.uc.alias",
        "deploy.requires.hitl.approval",
        "deploy.must.record.endpoint.state.snapshot",
    ),
)
# bv:templated:end id=skill


# bv:templated:start id=runner
CoordinatorCall = Callable[[dict[str, Any]], dict[str, Any]]
HitlApprovalCheck = Callable[[str], bool]
"""(endpoint_name) -> True if HITL has approved the deploy."""


def run_ml_serve_deploy(
    *,
    endpoint_name: str,
    model_full_name: str,
    alias: str,
    coordinator_call: CoordinatorCall,
    deploy_fn: DeployFn,
    hitl_check: HitlApprovalCheck,
    skill_id: str = "skill:ml.serve-deploy",
    audit_id: str | None = None,
    workload_size: str | None = None,
    scale_to_zero_enabled: bool | None = None,
) -> dict[str, Any]:
    audit_id = audit_id or str(uuid.uuid4())
    raw = coordinator_call(
        {
            "model_role": "serve_deploy",
            "endpoint_name": endpoint_name,
            "model_full_name": model_full_name,
            "alias": alias,
        }
    )
    spec = DeploySpec(
        endpoint_name=endpoint_name,
        model_full_name=model_full_name,
        alias=alias,
        workload_size=str(workload_size or raw.get("workload_size", "Small")),
        scale_to_zero_enabled=bool(
            scale_to_zero_enabled
            if scale_to_zero_enabled is not None
            else raw.get("scale_to_zero_enabled", True)
        ),
        served_model_name=str(raw.get("served_model_name", "")),
    )
    hitl_approved = hitl_check(endpoint_name)
    run, questions = deploy_endpoint(
        spec=spec,
        deploy_fn=deploy_fn,
        skill_id=skill_id,
        hitl_approved=hitl_approved,
        audit_id=audit_id,
    )
    return {
        "model_deployment_run": run,
        "questions": questions,
        "deploy_spec": spec,
    }


def run_ml_serve_deploy_alias_aware(
    *,
    endpoint_name: str,
    served_entities: Sequence[ServedEntity],
    alias_resolver: AliasResolverFn,
    deploy_fn: AliasAwareDeployFn,
    describe_fn: DescribeEndpointFn,
    hitl_check: HitlApprovalCheck,
    skill_id: str = "skill:ml.serve-deploy",
    audit_id: str | None = None,
) -> dict[str, Any]:
    """v0.7.6.4 alias-driven path (N151).

    Resolves every served_entity's UC alias to a concrete version,
    asserts the val-floor was passed, gates on HITL, and captures
    the typed ``ServingEndpointStateSnapshot`` on the audit row.
    """

    audit_id = audit_id or str(uuid.uuid4())
    spec = AliasAwareDeploySpec(
        endpoint_name=endpoint_name,
        served_entities=tuple(served_entities),
    )
    hitl_approved = hitl_check(endpoint_name)
    run, questions = deploy_endpoint_alias_aware(
        spec=spec,
        alias_resolver=alias_resolver,
        deploy_fn=deploy_fn,
        describe_fn=describe_fn,
        skill_id=skill_id,
        hitl_approved=hitl_approved,
        audit_id=audit_id,
    )
    return {
        "alias_aware_deploy_run": run,
        "questions": questions,
        "deploy_spec": spec,
    }


__all__ = [
    "AliasAwareDeployRun",
    "AliasAwareDeploySpec",
    "ModelDeploymentRun",
    "SKILL",
    "ServedEntity",
    "SYSTEM_PROMPT_SECTIONS",
    "run_ml_serve_deploy",
    "run_ml_serve_deploy_alias_aware",
]
# bv:templated:end id=runner
