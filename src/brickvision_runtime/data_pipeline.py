"""Data-pipeline runtime contracts used by Delta transform skills."""

from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Callable
from typing import Any

from brickvision_runtime.failures import Question, ReasonCode, question_from_failure


@dataclasses.dataclass(frozen=True)
class PipelineSpec:
    transform_id: str
    input_uris: tuple[str, ...]
    output_uri: str
    expected_output_schema: dict[str, str]
    description: str = ""


@dataclasses.dataclass(frozen=True)
class SqlSubmission:
    transform_id: str
    sql_text: str
    warehouse_id: str
    input_uris: tuple[str, ...]
    output_uri: str
    expected_output_schema: dict[str, str]


@dataclasses.dataclass(frozen=True)
class PySparkSubmission:
    transform_id: str
    transform_code: str
    input_uris: tuple[str, ...]
    output_uri: str
    expected_output_schema: dict[str, str]


@dataclasses.dataclass(frozen=True)
class JobOutcome:
    success: bool
    observed_schema: dict[str, str] = dataclasses.field(default_factory=dict)
    row_count: int | None = None
    run_id: str | None = None
    message: str = ""
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class DataPipelineRun:
    audit_id: str
    transform_id: str
    skill_id: str
    engine: str
    input_uris: tuple[str, ...]
    output_uri: str
    expected_output_schema: dict[str, str]
    observed_schema: dict[str, str]
    static_validated: bool
    binding_ok: bool
    execution_success: bool
    row_count: int | None
    run_id: str | None
    message: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class SqlCodegenResult:
    sql_text: str
    static_validated: bool
    forbidden_tokens: tuple[str, ...]
    questions: tuple[Question, ...] = ()


@dataclasses.dataclass(frozen=True)
class PySparkCodegenResult:
    code: str
    static_validated: bool
    forbidden_imports: tuple[str, ...]
    parse_error: str | None
    questions: tuple[Question, ...] = ()


CoordinatorCall = Callable[[dict[str, Any]], dict[str, Any]]
SubmitSql = Callable[[SqlSubmission], JobOutcome]
SubmitPySpark = Callable[[PySparkSubmission], JobOutcome]

_FORBIDDEN_SQL = ("DELETE", "DROP", "TRUNCATE")
_FORBIDDEN_IMPORT_ROOTS = {
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "httpx",
    "pickle",
    "shutil",
    "tempfile",
}


def sql_codegen(*, spec: PipelineSpec, coordinator_call: CoordinatorCall) -> SqlCodegenResult:
    raw = coordinator_call(
        {
            "model_role": "sql_codegen",
            "transform_id": spec.transform_id,
            "input_uris": list(spec.input_uris),
            "output_uri": spec.output_uri,
            "expected_output_schema": spec.expected_output_schema,
            "description": spec.description,
        }
    )
    sql_text = str(raw.get("sql_text", ""))
    forbidden = tuple(token for token in _FORBIDDEN_SQL if _has_sql_token(sql_text, token))
    questions = (
        _question(
            subject=spec.transform_id,
            details={"forbidden_tokens": forbidden},
            action="Regenerate SQL without DELETE, DROP, or TRUNCATE.",
        ),
    ) if forbidden else ()
    return SqlCodegenResult(
        sql_text=sql_text,
        static_validated=bool(sql_text.strip()) and not forbidden,
        forbidden_tokens=forbidden,
        questions=questions,
    )


def pyspark_codegen(
    *,
    spec: PipelineSpec,
    coordinator_call: CoordinatorCall,
) -> PySparkCodegenResult:
    raw = coordinator_call(
        {
            "model_role": "pyspark_codegen",
            "transform_id": spec.transform_id,
            "input_uris": list(spec.input_uris),
            "output_uri": spec.output_uri,
            "expected_output_schema": spec.expected_output_schema,
            "description": spec.description,
        }
    )
    code = str(raw.get("code", ""))
    try:
        tree = ast.parse(code)
        parse_error = None
    except SyntaxError as exc:
        tree = None
        parse_error = f"{exc.msg} at line {exc.lineno}"

    forbidden = tuple(sorted(_forbidden_imports(tree))) if tree is not None else ()
    questions: tuple[Question, ...] = ()
    if parse_error or forbidden:
        questions = (
            _question(
                subject=spec.transform_id,
                details={"parse_error": parse_error, "forbidden_imports": forbidden},
                action="Regenerate PySpark code that parses and does not import forbidden modules.",
            ),
        )
    return PySparkCodegenResult(
        code=code,
        static_validated=bool(code.strip()) and parse_error is None and not forbidden,
        forbidden_imports=forbidden,
        parse_error=parse_error,
        questions=questions,
    )


def run_sql_transform(
    *,
    submission: SqlSubmission,
    submit_sql: SubmitSql,
    skill_id: str,
    static_validated: bool,
    binding_ok: bool,
    audit_id: str,
) -> tuple[DataPipelineRun, tuple[Question, ...]]:
    if not static_validated or not binding_ok:
        outcome = JobOutcome(success=False, message="SQL transform blocked before execution.")
    else:
        outcome = submit_sql(submission)
    return _pipeline_run(
        audit_id=audit_id,
        transform_id=submission.transform_id,
        skill_id=skill_id,
        engine="sql",
        input_uris=submission.input_uris,
        output_uri=submission.output_uri,
        expected_output_schema=submission.expected_output_schema,
        static_validated=static_validated,
        binding_ok=binding_ok,
        outcome=outcome,
    )


def run_pyspark_transform(
    *,
    submission: PySparkSubmission,
    submit_job: SubmitPySpark,
    skill_id: str,
    static_validated: bool,
    binding_ok: bool,
    audit_id: str,
) -> tuple[DataPipelineRun, tuple[Question, ...]]:
    if not static_validated or not binding_ok:
        outcome = JobOutcome(success=False, message="PySpark transform blocked before execution.")
    else:
        outcome = submit_job(submission)
    return _pipeline_run(
        audit_id=audit_id,
        transform_id=submission.transform_id,
        skill_id=skill_id,
        engine="pyspark",
        input_uris=submission.input_uris,
        output_uri=submission.output_uri,
        expected_output_schema=submission.expected_output_schema,
        static_validated=static_validated,
        binding_ok=binding_ok,
        outcome=outcome,
    )


def _pipeline_run(
    *,
    audit_id: str,
    transform_id: str,
    skill_id: str,
    engine: str,
    input_uris: tuple[str, ...],
    output_uri: str,
    expected_output_schema: dict[str, str],
    static_validated: bool,
    binding_ok: bool,
    outcome: JobOutcome,
) -> tuple[DataPipelineRun, tuple[Question, ...]]:
    schema_ok = _schemas_match(expected_output_schema, outcome.observed_schema)
    success = bool(outcome.success and schema_ok)
    questions = () if success else (
        _question(
            subject=transform_id,
            details={
                "engine": engine,
                "execution_success": outcome.success,
                "expected_output_schema": expected_output_schema,
                "observed_schema": outcome.observed_schema,
                "binding_ok": binding_ok,
                "static_validated": static_validated,
            },
            action="Fix the transform runtime, binding, or schema drift before enabling execution.",
        ),
    )
    return (
        DataPipelineRun(
            audit_id=audit_id,
            transform_id=transform_id,
            skill_id=skill_id,
            engine=engine,
            input_uris=input_uris,
            output_uri=output_uri,
            expected_output_schema=dict(expected_output_schema),
            observed_schema=dict(outcome.observed_schema),
            static_validated=static_validated,
            binding_ok=binding_ok,
            execution_success=success,
            row_count=outcome.row_count,
            run_id=outcome.run_id,
            message=outcome.message,
            metadata=dict(outcome.metadata),
        ),
        questions,
    )


def _has_sql_token(sql_text: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", sql_text, flags=re.IGNORECASE) is not None


def _forbidden_imports(tree: ast.AST | None) -> set[str]:
    if tree is None:
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports & _FORBIDDEN_IMPORT_ROOTS


def _schemas_match(expected: dict[str, str], observed: dict[str, str]) -> bool:
    if not expected:
        return bool(observed)
    normalize = lambda value: str(value).strip().upper()
    return {k: normalize(v) for k, v in expected.items()} == {
        k: normalize(v) for k, v in observed.items()
    }


def _question(*, subject: str, details: dict[str, Any], action: str) -> Question:
    return question_from_failure(
        reason=ReasonCode.WRITE_TARGET_CATALOG_NOT_BOUND_RW,
        subject=subject,
        raised_by="brickvision_runtime.data_pipeline",
        details=details,
        suggested_next_action=action,
    )


__all__ = [
    "DataPipelineRun",
    "JobOutcome",
    "PipelineSpec",
    "PySparkCodegenResult",
    "PySparkSubmission",
    "SqlCodegenResult",
    "SqlSubmission",
    "pyspark_codegen",
    "run_pyspark_transform",
    "run_sql_transform",
    "sql_codegen",
]
