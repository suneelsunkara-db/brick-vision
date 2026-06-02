"""LLM-backed Layer-0 skill: ``skill:delta.pyspark-transform`` (N136).

Pipeline (per docs/11-skill-catalog.md §9.1.2):
    pyspark_codegen -> static validation (ast.parse + forbidden-import)
                    -> uc_bindings pre-flight
                    -> Serverless Job submit
                    -> typed DataPipelineRun audit row + Questions
"""

from __future__ import annotations

# bv:templated:start id=imports
import uuid
from collections.abc import Callable
from typing import Any

from brickvision_runtime.data_pipeline import (
    DataPipelineRun,
    JobOutcome,
    PipelineSpec,
    PySparkSubmission,
    pyspark_codegen,
    run_pyspark_transform,
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
            "You write a single PySpark function `def transform(spark, "
            "inputs: dict[str, DataFrame]) -> DataFrame` that produces "
            "the target table."
        ),
    ),
    SystemPromptSection(
        id="schema_contract",
        altitude="high",
        text=(
            "Return JSON {code: <python source>}. The code MUST match the "
            "expected_output_schema exactly — no extra columns, no missing "
            "columns. Use Spark types only (no Python int/str return types)."
        ),
    ),
    SystemPromptSection(
        id="forbidden",
        altitude="high",
        text=(
            "Do not import subprocess, os (use os.path is OK), socket, "
            "urllib, requests, httpx, pickle, shutil, tempfile, or any "
            "network/filesystem-mutating module. Do not call open() with "
            "absolute paths. Do not shell out."
        ),
    ),
    SystemPromptSection(
        id="discipline",
        altitude="medium",
        text=(
            "If the spec is ambiguous, return code that raises NotImplementedError "
            "with a structured message rather than guessing."
        ),
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:delta.pyspark-transform",
    version="0.1.0",
    model_role="pyspark_codegen",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=[
        "tool:data_pipeline.submit_pyspark_job",
        "tool:uc.bindings_check",
    ],
    behavior_constraints=BehaviorConstraints(
        must_emit_evidence_chain=True,
        extra={
            "must_call_uc_bindings_pre_flight": True,
            "must_validate_via_ast_parse": True,
            "must_emit_data_pipeline_run_audit_row": True,
        },
    ),
    max_turns=4,
    constitutional=(
        "no.shell.out",
        "no.network.access",
        "write.target.catalog.must.be.bound.read.write.to.executing.workspace",
    ),
)
# bv:templated:end id=skill


# bv:templated:start id=runner
SubmitJob = Callable[[PySparkSubmission], JobOutcome]
CoordinatorCall = Callable[[dict[str, Any]], dict[str, Any]]
BindingCheck = Callable[[str], bool]


def run_delta_pyspark_transform(
    *,
    spec: PipelineSpec,
    coordinator_call: CoordinatorCall,
    submit_job: SubmitJob,
    binding_check: BindingCheck,
    skill_id: str = "skill:delta.pyspark-transform",
    audit_id: str | None = None,
) -> dict[str, Any]:
    """Run the full pipeline.

    Returns a dict with the generated code, the typed
    ``DataPipelineRun`` audit row, and any Questions raised.
    """

    audit_id = audit_id or str(uuid.uuid4())
    codegen = pyspark_codegen(spec=spec, coordinator_call=coordinator_call)
    questions: list[Any] = list(codegen.questions)

    if not codegen.static_validated:
        return {
            "code": codegen.code,
            "static_validated": False,
            "forbidden_imports": list(codegen.forbidden_imports),
            "parse_error": codegen.parse_error,
            "data_pipeline_run": None,
            "questions": questions,
        }

    binding_ok = binding_check(spec.output_uri)

    submission = PySparkSubmission(
        transform_id=spec.transform_id,
        transform_code=codegen.code,
        input_uris=tuple(spec.input_uris),
        output_uri=spec.output_uri,
        expected_output_schema=dict(spec.expected_output_schema),
    )
    run, run_questions = run_pyspark_transform(
        submission=submission,
        submit_job=submit_job,
        skill_id=skill_id,
        static_validated=True,
        binding_ok=binding_ok,
        audit_id=audit_id,
    )
    questions.extend(run_questions)

    return {
        "code": codegen.code,
        "static_validated": True,
        "forbidden_imports": [],
        "parse_error": None,
        "data_pipeline_run": run,
        "questions": questions,
    }


__all__ = [
    "DataPipelineRun",
    "SKILL",
    "SYSTEM_PROMPT_SECTIONS",
    "run_delta_pyspark_transform",
]
# bv:templated:end id=runner
