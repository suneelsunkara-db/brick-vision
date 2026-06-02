"""LLM-backed Layer-0 skill: ``skill:delta.sql-transform`` (N137)."""

from __future__ import annotations

# bv:templated:start id=imports
import uuid
from collections.abc import Callable
from typing import Any

from brickvision_runtime.data_pipeline import (
    DataPipelineRun,
    JobOutcome,
    PipelineSpec,
    SqlSubmission,
    run_sql_transform,
    sql_codegen,
)
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
        text=(
            "You write a single SQL statement that materializes the target "
            "table. Use CREATE OR REPLACE TABLE <output> AS SELECT … or "
            "MERGE INTO when an upstream merge key is provided."
        ),
    ),
    SystemPromptSection(
        id="schema_contract",
        altitude="high",
        text=(
            "Return JSON {sql_text: <sql>}. The output schema MUST match "
            "expected_output_schema exactly. No extra columns; no missing "
            "columns; types must match Spark SQL canonical names."
        ),
    ),
    SystemPromptSection(
        id="forbidden",
        altitude="high",
        text=(
            "Do not emit DELETE, DROP, or TRUNCATE. The harness rejects "
            "any of these tokens before the warehouse sees the SQL."
        ),
    ),
    SystemPromptSection(
        id="discipline",
        altitude="medium",
        text=(
            "Never use SELECT *. Always project the columns explicitly so "
            "schema drift is detectable."
        ),
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:delta.sql-transform",
    version="0.1.0",
    model_role="sql_codegen",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:data_pipeline.submit_sql", "tool:uc.bindings_check"],
    behavior_constraints=BehaviorConstraints(
        must_emit_evidence_chain=True,
        extra={
            "must_call_uc_bindings_pre_flight": True,
            "must_validate_via_forbidden_token_lint": True,
            "must_emit_data_pipeline_run_audit_row": True,
            "must_use_explicit_projection": True,
        },
    ),
    max_turns=4,
    constitutional=(
        "no.delete.no.drop.no.truncate",
        "write.target.catalog.must.be.bound.read.write.to.executing.workspace",
    ),
)
# bv:templated:end id=skill


# bv:templated:start id=runner
SubmitSql = Callable[[SqlSubmission], JobOutcome]
CoordinatorCall = Callable[[dict[str, Any]], dict[str, Any]]
BindingCheck = Callable[[str], bool]


def run_delta_sql_transform(
    *,
    spec: PipelineSpec,
    warehouse_id: str,
    coordinator_call: CoordinatorCall,
    submit_sql: SubmitSql,
    binding_check: BindingCheck,
    skill_id: str = "skill:delta.sql-transform",
    audit_id: str | None = None,
) -> dict[str, Any]:
    audit_id = audit_id or str(uuid.uuid4())
    codegen = sql_codegen(spec=spec, coordinator_call=coordinator_call)
    questions: list[Any] = list(codegen.questions)

    if not codegen.static_validated:
        return {
            "sql_text": codegen.sql_text,
            "static_validated": False,
            "forbidden_tokens": list(codegen.forbidden_tokens),
            "data_pipeline_run": None,
            "questions": questions,
        }

    binding_ok = binding_check(spec.output_uri)
    submission = SqlSubmission(
        transform_id=spec.transform_id,
        sql_text=codegen.sql_text,
        warehouse_id=warehouse_id,
        input_uris=tuple(spec.input_uris),
        output_uri=spec.output_uri,
        expected_output_schema=dict(spec.expected_output_schema),
    )
    run, run_questions = run_sql_transform(
        submission=submission,
        submit_sql=submit_sql,
        skill_id=skill_id,
        static_validated=True,
        binding_ok=binding_ok,
        audit_id=audit_id,
    )
    questions.extend(run_questions)
    return {
        "sql_text": codegen.sql_text,
        "static_validated": True,
        "forbidden_tokens": [],
        "data_pipeline_run": run,
        "questions": questions,
    }


__all__ = [
    "DataPipelineRun",
    "SKILL",
    "SYSTEM_PROMPT_SECTIONS",
    "run_delta_sql_transform",
]
# bv:templated:end id=runner
