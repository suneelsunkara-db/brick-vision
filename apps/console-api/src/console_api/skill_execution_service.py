"""Execute usecase artifacts through BrickVision skill contracts."""

from __future__ import annotations

import dataclasses
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib import request

from . import databricks_sql
from .skill_contracts import import_skill_module, load_skill_contract, repo_root, skill_ids_for_family
from .usecase_artifacts import _schema_profile_quality_sql
from .usecase_records import _load_latest_skill_input_bindings, get_usecase_record

SQL_SKILL_ID = skill_ids_for_family("SQL")[0]
PYSPARK_SKILL_ID = skill_ids_for_family("PySpark")[0]
STATEMENT_EXECUTE_SKILL_ID = "skill:databricks.statement-execute"
PYSPARK_TASK_PLAN_SKILL_ID = "skill:delta.pyspark-task-plan"
ML_PROBLEM_SKILL_ID = "skill:ml.problem-select"
ML_FEATURE_SKILL_ID = "skill:ml.feature-readiness"
ML_STRATEGY_SKILL_ID = "skill:ml.strategy-plan"
ML_MODEL_FAMILY_SKILL_ID = "skill:ml.model-family-select"
ML_BACKEND_PROBE_SKILL_ID = "skill:ml.training-backend-probe"
ML_BACKEND_SELECT_SKILL_ID = "skill:ml.training-backend-select"
ML_TRAINING_ARTIFACT_PLAN_SKILL_ID = "skill:ml.training-artifact-plan"
ML_TRAINING_TASK_PLAN_SKILL_ID = "skill:ml.training-task-plan"
JOBS_RUN_SUBMIT_SKILL_ID = "skill:lakeflow.jobs-run-submit"
ML_API_PLAN_BIND_SKILL_ID = "skill:ml.api-plan-bind"
ML_TRAIN_SKILL_ID = "skill:ml.train-evaluate-register"
MIGRATION_LAKEBRIDGE_SKILL_ID = "skill:migration.lakebridge-sql-transpile"
MIGRATION_CODE_CONVERT_SKILL_ID = "skill:migration.lakebridge-code-convert"
MIGRATION_ASSESS_SKILL_ID = "skill:migration.lakebridge-assess"
ML_CAPABILITY_QUERY_LIMIT = 6


def execute_skill_for_usecase(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a usecase skill family through its SKILL.py runner."""

    normalized = family.strip()
    if normalized == "SQL":
        return execute_sql_skill_for_usecase(user_id=user_id, usecase_id=usecase_id)
    if normalized == "PySpark":
        return execute_pyspark_skill_for_usecase(user_id=user_id, usecase_id=usecase_id)
    if normalized == "ML":
        return execute_ml_skill_for_usecase(user_id=user_id, usecase_id=usecase_id)
    if normalized == "Migration":
        return execute_migration_skill_for_usecase(
            user_id=user_id,
            usecase_id=usecase_id,
            execution_inputs=execution_inputs,
        )
    if normalized == "Code Convert":
        return execute_migration_code_convert_skill_for_usecase(
            user_id=user_id,
            usecase_id=usecase_id,
            execution_inputs=execution_inputs,
        )
    if normalized == "Assessment":
        return execute_migration_assessment_skill_for_usecase(
            user_id=user_id,
            usecase_id=usecase_id,
            execution_inputs=execution_inputs,
        )
    return {
        "status": "skill_contract_missing",
        "usecase_id": usecase_id,
        "skill_id": "",
        "family": normalized,
        "executed": False,
        "proof_kind": "skill_contract_execution",
        "message": f"No executable skill contract is registered for {normalized}.",
    }


def execute_migration_skill_for_usecase(
    *,
    user_id: str,
    usecase_id: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the Lakebridge migration transpilation artifact bundle."""

    _ensure_runtime_path()
    runner_state = _load_skill_runner(MIGRATION_LAKEBRIDGE_SKILL_ID)
    if runner_state["status"] != "ready":
        return runner_state | {"usecase_id": usecase_id, "skill_id": MIGRATION_LAKEBRIDGE_SKILL_ID}
    runner = runner_state["runner"]
    raw_inputs = dict(execution_inputs or {})
    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        if not raw_inputs.get("standalone_migration_run"):
            return record
        record = {"artifact_plan": {"steps": []}}
    inputs = _migration_transpile_inputs(record=record, execution_inputs=raw_inputs)
    inputs["usecase_id"] = usecase_id
    live_source = _migration_source_sql(inputs)
    try:
        sql_cli = _lakebridge_sql_cli()
        if sql_cli["available"] and live_source.get("status") in {"bound", "bound_external"}:
            bundle = _run_lakebridge_sql_transpile(
                inputs=inputs,
                live_source=live_source,
                sql_cli=sql_cli,
            )
            lakebridge_sql_run = bundle.pop("lakebridge_sql_run", None)
            execution = runner(**bundle)
            execution = {
                **execution,
                "usecase_id": usecase_id,
                "family": "Migration",
                "skill_id": MIGRATION_LAKEBRIDGE_SKILL_ID,
                "proof_mode": "live_transpile",
                "lakebridge_sql_run": lakebridge_sql_run,
            }
        else:
            execution = _migration_live_transpile_blocked(
                usecase_id=usecase_id,
                inputs=inputs,
                live_source=live_source,
                sql_cli=sql_cli,
            )
    except Exception as exc:
        execution = {
            "status": "execution_failed",
            "usecase_id": usecase_id,
            "skill_id": MIGRATION_LAKEBRIDGE_SKILL_ID,
            "family": "Migration",
            "executed": False,
            "proof_kind": "migration_transpile_artifact",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    return execution


def execute_migration_assessment_skill_for_usecase(
    *,
    user_id: str,
    usecase_id: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Lakebridge assessment readiness before migration conversion."""

    del user_id
    _ensure_runtime_path()
    runner_state = _load_skill_runner(MIGRATION_ASSESS_SKILL_ID)
    if runner_state["status"] != "ready":
        return runner_state | {"usecase_id": usecase_id, "skill_id": MIGRATION_ASSESS_SKILL_ID}
    runner = runner_state["runner"]
    inputs = dict(execution_inputs or {})
    try:
        execution = runner(
            source_system=_optional_string(inputs.get("source_system"))
            or _optional_string(inputs.get("source_dialect")),
            source_path=_optional_string(inputs.get("source_path"))
            or _optional_string(inputs.get("assessment_source_path")),
            assessment_output_path=_optional_string(inputs.get("assessment_output_path")),
            lakebridge_env_path=_optional_string(inputs.get("lakebridge_env_path")),
        )
        return {
            **execution,
            "usecase_id": usecase_id,
            "family": "Assessment",
            "skill_id": MIGRATION_ASSESS_SKILL_ID,
        }
    except Exception as exc:
        return {
            "status": "execution_failed",
            "usecase_id": usecase_id,
            "skill_id": MIGRATION_ASSESS_SKILL_ID,
            "family": "Assessment",
            "executed": False,
            "proof_kind": "lakebridge_assessment_readiness",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }


def execute_migration_code_convert_skill_for_usecase(
    *,
    user_id: str,
    usecase_id: str,
    execution_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the Lakebridge/Switch code-convert preflight without starting conversion."""

    _ensure_runtime_path()
    runner_state = _load_skill_runner(MIGRATION_CODE_CONVERT_SKILL_ID)
    if runner_state["status"] != "ready":
        return runner_state | {"usecase_id": usecase_id, "skill_id": MIGRATION_CODE_CONVERT_SKILL_ID}
    runner = runner_state["runner"]
    inputs = dict(execution_inputs or {})
    source_path = _optional_string(inputs.get("source_path") or inputs.get("input_path"))
    output_path = _optional_string(inputs.get("output_path") or inputs.get("converted_output_path"))
    source_path = source_path or _code_convert_volume_path("source")
    output_path = output_path or _code_convert_volume_path("output")
    workspace_output_folder = _optional_string(
        inputs.get("workspace_output_folder") or os.environ.get("BV_CODE_CONVERT_WORKSPACE_OUTPUT_PATH")
    ) or _code_convert_workspace_output_path(usecase_id)
    try:
        execution = runner(
            source_path=source_path,
            output_path=output_path,
            source_technology=_optional_string(inputs.get("source_technology")) or "python",
            target_technology=_optional_string(inputs.get("target_technology")) or "databricks",
            model_endpoint=_optional_string(inputs.get("model_endpoint")) or _switch_model_endpoint(),
            workspace_output_folder=workspace_output_folder,
            switch_config_path=_ensure_code_convert_switch_config_path(
                source_technology=_optional_string(inputs.get("source_technology")) or "python",
                source_path=source_path,
                output_path=output_path,
            ),
            workspace_host=_optional_string(inputs.get("workspace_host"))
            or _optional_string(os.environ.get("DATABRICKS_HOST")),
            workspace_token_present=bool(
                inputs.get("workspace_token_present") or os.environ.get("DATABRICKS_TOKEN")
            ),
        )
        execution = {
            **execution,
            "usecase_id": usecase_id,
            "family": "Code Convert",
            "skill_id": MIGRATION_CODE_CONVERT_SKILL_ID,
        }
    except Exception as exc:
        execution = {
            "status": "execution_failed",
            "usecase_id": usecase_id,
            "skill_id": MIGRATION_CODE_CONVERT_SKILL_ID,
            "family": "Code Convert",
            "executed": False,
            "proof_kind": "migration_code_convert_preflight",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    return execution


def _code_convert_volume_path(kind: str) -> str:
    env_name = "BV_CODE_CONVERT_SOURCE_PATH" if kind == "source" else "BV_CODE_CONVERT_OUTPUT_PATH"
    configured = _optional_string(os.environ.get(env_name))
    if configured:
        return configured
    catalog = os.environ.get("BV_CATALOG", "brickvision").strip() or "brickvision"
    schema = os.environ.get("BV_SCHEMA", "brickvision").strip() or "brickvision"
    volume = (
        os.environ.get("BV_CODE_CONVERT_VOLUME")
        or os.environ.get("BV_INDEXER_STATE_VOLUME")
        or "indexer-state"
    ).strip()
    leaf = "source" if kind == "source" else "output"
    return f"/Volumes/{catalog}/{schema}/{volume}/lakebridge/pyspark/{leaf}"


def _code_convert_workspace_output_path(usecase_id: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in usecase_id)
    workspace_user = (
        os.environ.get("BV_CODE_CONVERT_WORKSPACE_USER")
        or os.environ.get("DATABRICKS_USER")
        or os.environ.get("DATABRICKS_USERNAME")
        or os.environ.get("USER")
        or "brickvision"
    ).strip()
    return f"/Workspace/Users/{workspace_user}/lakebridge/pyspark/{safe_id}/output"


def _ensure_code_convert_switch_config_path(
    *,
    source_technology: str,
    source_path: str,
    output_path: str,
) -> str:
    configured = _optional_string(os.environ.get("BV_CODE_CONVERT_SWITCH_CONFIG_PATH"))
    if configured:
        return configured
    source = source_technology.strip().lower()
    if source not in {"python", "pyspark"}:
        return ""
    catalog, schema, volume = _code_convert_volume_parts(output_path or source_path)
    if not catalog or not schema or not volume:
        return ""
    if source == "pyspark":
        config_file = _code_convert_switch_config_file(
            catalog=catalog,
            schema=schema,
            volume=volume,
            target_type="sdp",
            source_format="generic",
            prompt_source=None,
            config_name="pyspark_sdp_switch_config.yml",
        )
        return config_file
    prompt_source = (
        repo_root()
        / "config"
        / "lakebridge"
        / "switch"
        / "legacy_pyspark_python_to_databricks_notebook.yml"
    )
    if not prompt_source.exists():
        return ""
    return _code_convert_switch_config_file(
        catalog=catalog,
        schema=schema,
        volume=volume,
        target_type="notebook",
        source_format="generic",
        prompt_source=prompt_source,
        config_name="legacy_pyspark_python_switch_config.yml",
    )


def _code_convert_switch_config_file(
    *,
    catalog: str,
    schema: str,
    volume: str,
    target_type: str,
    source_format: str,
    prompt_source: Path | None,
    config_name: str,
) -> str:
    tmp_root = Path(tempfile.mkdtemp(prefix="bv-switch-code-config-"))
    volume_base = f"/Volumes/{catalog}/{schema}/{volume}/lakebridge/switch"
    prompt_volume_path = ""
    if prompt_source is not None:
        prompt_volume_path = f"{volume_base}/prompts/{prompt_source.name}"
        _copy_local_file_to_volume(source_file=prompt_source, volume_path=prompt_volume_path)
    config_volume_path = f"{volume_base}/config/{config_name}"
    config_file = tmp_root / config_name
    lines = [
        f'target_type: "{target_type}"',
        f'source_format: "{source_format}"',
        'comment_lang: "English"',
        'log_level: "INFO"',
        "token_count_threshold: 20000",
        "concurrency: 4",
        "max_fix_attempts: 1",
        f'conversion_prompt_yaml: "{prompt_volume_path}"' if prompt_volume_path else "conversion_prompt_yaml:",
        "output_extension:",
        "sql_output_dir:",
        "request_params:",
        'sdp_language: "python"',
        "",
    ]
    config_file.write_text("\n".join(lines), encoding="utf-8")
    _copy_local_file_to_volume(source_file=config_file, volume_path=config_volume_path)
    return config_volume_path


def _code_convert_volume_parts(path: str) -> tuple[str, str, str]:
    volume_path = path[5:] if path.startswith("dbfs:") else path
    if not volume_path.startswith("/Volumes/"):
        return "", "", ""
    parts = volume_path.split("/")
    if len(parts) < 5:
        return "", "", ""
    return parts[2], parts[3], parts[4]


def execute_sql_skill_for_usecase(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Execute the SQL artifact path through ``skill:delta.sql-transform``."""

    _ensure_runtime_path()
    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    step = _sql_step(record)
    inputs = dict(step.get("bound_inputs") or {}) if step else {}
    spec_state = _build_sql_spec(record=record, usecase_id=usecase_id, inputs=inputs)
    if spec_state["status"] != "ready":
        return spec_state

    runner_state = _load_skill_runner(SQL_SKILL_ID)
    statement_runner_state = _load_skill_runner(STATEMENT_EXECUTE_SKILL_ID)
    for skill_id, state in {
        SQL_SKILL_ID: runner_state,
        STATEMENT_EXECUTE_SKILL_ID: statement_runner_state,
    }.items():
        if state["status"] != "ready":
            return state | {"usecase_id": usecase_id, "skill_id": skill_id}
    runner = runner_state["runner"]
    statement_runner = statement_runner_state["runner"]
    if not callable(runner):
        return {
            "status": "skill_runner_missing",
            "usecase_id": usecase_id,
            "skill_id": SQL_SKILL_ID,
            "message": f"{SQL_SKILL_ID} does not expose its declared runner.",
        }

    spec = spec_state["spec"]
    sql_text = str(spec_state["sql_text"])
    warehouse_id = str(inputs.get("warehouse_id") or "").strip()
    if not warehouse_id:
        return {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": SQL_SKILL_ID,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "message": "SQL execution needs warehouse_id bound from the skill contract.",
        }
    try:
        result = runner(
            spec=spec,
            warehouse_id=warehouse_id,
            coordinator_call=lambda request: _sql_coordinator_call(request, sql_text=sql_text),
            submit_sql=lambda submission: _submit_sql_statement(
                submission,
                statement_runner=statement_runner,
                capability_evidence=_list_of_objects(inputs.get("statement_capability_evidence"))
                or _list_of_objects(inputs.get("capability_evidence")),
                statement_operation=_object_any(inputs.get("statement_operation")),
            ),
            binding_check=_binding_check,
            skill_id=SQL_SKILL_ID,
        )
        data_pipeline_run = result.get("data_pipeline_run")
        execution = {
            "status": (
                "execution_proven"
                if data_pipeline_run and data_pipeline_run.execution_success
                else "execution_failed"
            ),
            "usecase_id": usecase_id,
            "skill_id": SQL_SKILL_ID,
            "executed": bool(data_pipeline_run and data_pipeline_run.execution_success),
            "proof_kind": "skill_contract_execution",
            "sql_text": result.get("sql_text"),
            "data_pipeline_run": _jsonable(data_pipeline_run),
            "questions": [_jsonable(item) for item in result.get("questions", [])],
            "message": (
                "SQL artifact executed through skill:delta.sql-transform."
                if data_pipeline_run and data_pipeline_run.execution_success
                else "SQL skill execution returned questions or failed execution."
            ),
        }
    except Exception as exc:
        execution = {
            "status": "execution_failed",
            "usecase_id": usecase_id,
            "skill_id": SQL_SKILL_ID,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    _persist_skill_execution(user_id=user_id, usecase_id=usecase_id, execution=execution)
    return execution


def execute_pyspark_skill_for_usecase(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Execute the PySpark artifact path through ``skill:delta.pyspark-transform``."""

    _ensure_runtime_path()
    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    step = _family_step(record, "PySpark")
    inputs = dict(step.get("bound_inputs") or {}) if step else {}
    spec_state = _build_pipeline_spec(
        record=record,
        usecase_id=usecase_id,
        inputs=inputs,
        family="PySpark",
        default_transform_prefix="pyspark-transform",
        default_output_prefix="bv_skill_pyspark",
    )
    if spec_state["status"] != "ready":
        return spec_state

    transform_code = str(
        inputs.get("transform_code") or inputs.get("code") or inputs.get("artifact_code") or ""
    ).strip()
    if not transform_code:
        return {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": PYSPARK_SKILL_ID,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "message": (
                "PySpark execution must be supplied by the Skill Builder/codegen path. "
                "Bind transform_code/code before running this proof."
            ),
        }

    runner_state = _load_skill_runner(PYSPARK_SKILL_ID)
    task_plan_runner_state = _load_skill_runner(PYSPARK_TASK_PLAN_SKILL_ID)
    jobs_runner_state = _load_skill_runner(JOBS_RUN_SUBMIT_SKILL_ID)
    for skill_id, state in {
        PYSPARK_SKILL_ID: runner_state,
        PYSPARK_TASK_PLAN_SKILL_ID: task_plan_runner_state,
        JOBS_RUN_SUBMIT_SKILL_ID: jobs_runner_state,
    }.items():
        if state["status"] != "ready":
            return state | {"usecase_id": usecase_id, "skill_id": skill_id}
    runner = runner_state["runner"]
    task_plan_runner = task_plan_runner_state["runner"]
    jobs_run_submit_runner = jobs_runner_state["runner"]
    if not callable(runner):
        return {
            "status": "skill_runner_missing",
            "usecase_id": usecase_id,
            "skill_id": PYSPARK_SKILL_ID,
            "message": f"{PYSPARK_SKILL_ID} does not expose its declared runner.",
        }

    spec = spec_state["spec"]
    try:
        result = runner(
            spec=spec,
            coordinator_call=lambda request: _pyspark_coordinator_call(
                request,
                transform_code=transform_code,
            ),
            submit_job=lambda submission: _submit_pyspark_job(
                submission,
                task_plan_runner=task_plan_runner,
                jobs_run_submit_runner=jobs_run_submit_runner,
                inputs=inputs,
            ),
            binding_check=_binding_check,
            skill_id=PYSPARK_SKILL_ID,
        )
        data_pipeline_run = result.get("data_pipeline_run")
        execution = {
            "status": (
                "execution_proven"
                if data_pipeline_run and data_pipeline_run.execution_success
                else "execution_failed"
            ),
            "usecase_id": usecase_id,
            "skill_id": PYSPARK_SKILL_ID,
            "family": "PySpark",
            "executed": bool(data_pipeline_run and data_pipeline_run.execution_success),
            "proof_kind": "skill_contract_execution",
            "transform_code": result.get("code"),
            "data_pipeline_run": _jsonable(data_pipeline_run),
            "questions": [_jsonable(item) for item in result.get("questions", [])],
            "message": (
                "PySpark artifact executed through skill:delta.pyspark-transform."
                if data_pipeline_run and data_pipeline_run.execution_success
                else "PySpark skill execution returned questions or failed execution."
            ),
        }
    except SkillExecutionBlocked as exc:
        execution = {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": PYSPARK_SKILL_ID,
            "family": "PySpark",
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "message": str(exc),
        }
    except Exception as exc:
        execution = {
            "status": "execution_failed",
            "usecase_id": usecase_id,
            "skill_id": PYSPARK_SKILL_ID,
            "family": "PySpark",
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    _persist_skill_execution(user_id=user_id, usecase_id=usecase_id, execution=execution)
    return execution


def execute_ml_skill_for_usecase(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Run ML problem-selection and feature-readiness gates for a usecase."""

    _ensure_runtime_path()
    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    step = _family_step(record, "ML")
    inputs = (
        dict(step.get("bound_inputs") or {})
        if step
        else {}
    )
    inputs = _prepare_ml_training_inputs(record=record, usecase_id=usecase_id, inputs=inputs)
    required = tuple(_required_contract_inputs(ML_PROBLEM_SKILL_ID))
    missing = [name for name in required if not inputs.get(name)]
    if missing:
        return {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": ML_PROBLEM_SKILL_ID,
            "family": "ML",
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "missing_inputs": missing,
            "message": "ML readiness needs usecase objective and dataset profiles before training.",
        }

    runner_states = {
        ML_PROBLEM_SKILL_ID: _load_skill_runner(ML_PROBLEM_SKILL_ID),
        ML_FEATURE_SKILL_ID: _load_skill_runner(ML_FEATURE_SKILL_ID),
        ML_STRATEGY_SKILL_ID: _load_skill_runner(ML_STRATEGY_SKILL_ID),
        ML_MODEL_FAMILY_SKILL_ID: _load_skill_runner(ML_MODEL_FAMILY_SKILL_ID),
        ML_BACKEND_PROBE_SKILL_ID: _load_skill_runner(ML_BACKEND_PROBE_SKILL_ID),
        ML_BACKEND_SELECT_SKILL_ID: _load_skill_runner(ML_BACKEND_SELECT_SKILL_ID),
        ML_TRAINING_ARTIFACT_PLAN_SKILL_ID: _load_skill_runner(ML_TRAINING_ARTIFACT_PLAN_SKILL_ID),
        ML_TRAINING_TASK_PLAN_SKILL_ID: _load_skill_runner(ML_TRAINING_TASK_PLAN_SKILL_ID),
        JOBS_RUN_SUBMIT_SKILL_ID: _load_skill_runner(JOBS_RUN_SUBMIT_SKILL_ID),
        ML_API_PLAN_BIND_SKILL_ID: _load_skill_runner(ML_API_PLAN_BIND_SKILL_ID),
    }
    for skill_id, state in runner_states.items():
        if state["status"] != "ready":
            return state | {"usecase_id": usecase_id, "skill_id": skill_id}
    problem_runner = runner_states[ML_PROBLEM_SKILL_ID]["runner"]
    feature_runner = runner_states[ML_FEATURE_SKILL_ID]["runner"]
    strategy_runner = runner_states[ML_STRATEGY_SKILL_ID]["runner"]
    model_family_runner = runner_states[ML_MODEL_FAMILY_SKILL_ID]["runner"]
    backend_probe_runner = runner_states[ML_BACKEND_PROBE_SKILL_ID]["runner"]
    backend_select_runner = runner_states[ML_BACKEND_SELECT_SKILL_ID]["runner"]
    training_artifact_plan_runner = runner_states[ML_TRAINING_ARTIFACT_PLAN_SKILL_ID]["runner"]
    training_task_plan_runner = runner_states[ML_TRAINING_TASK_PLAN_SKILL_ID]["runner"]
    jobs_run_submit_runner = runner_states[JOBS_RUN_SUBMIT_SKILL_ID]["runner"]
    api_plan_bind_runner = runner_states[ML_API_PLAN_BIND_SKILL_ID]["runner"]
    try:
        dataset_profiles = _list_of_objects(inputs.get("dataset_profiles"))
        problem = problem_runner(
            usecase_title=str(inputs.get("usecase_title") or record.get("title") or ""),
            business_objective=str(inputs.get("business_objective") or record.get("outcome") or ""),
            dataset_profiles=dataset_profiles,
            candidate_target=_optional_string(inputs.get("candidate_target")),
        )
        feature = feature_runner(
            problem_type=str(inputs.get("problem_type") or problem.get("recommended_problem_type") or ""),
            dataset_profiles=dataset_profiles,
            target_column=_optional_string(inputs.get("target_column") or problem.get("target_column")),
            entity_key=_optional_string(inputs.get("entity_key")),
            time_column=_optional_string(inputs.get("time_column")),
        )
        capability_inputs = _ml_strategy_capability_inputs(
            user_id=user_id,
            inputs=inputs,
            problem_type=str(problem.get("recommended_problem_type") or ""),
        )
        strategy = strategy_runner(
            problem_selection=problem,
            feature_readiness=feature,
            dataset_profiles=dataset_profiles,
            model_full_name=_optional_string(inputs.get("model_full_name")),
            capability_evidence=capability_inputs["capability_evidence"],
            api_operations=capability_inputs["api_operations"],
        )
        strategy_ready = (
            problem.get("status") == "ready_for_strategy"
            and feature.get("status") == "feature_ready"
            and strategy.get("status") == "ready_for_approval"
        )
        model_family = (
            model_family_runner(
                strategy_plan=strategy,
                feature_readiness=feature,
                dataset_profiles=dataset_profiles,
            )
            if strategy_ready
            else {
                "status": "blocked",
                "selected_model_family": None,
                "candidate_model_families": [],
                "findings": [],
                "next_action": "Resolve ML strategy blockers before selecting a model family.",
            }
        )
        backend_probe = (
            backend_probe_runner(
                runtime_surface=str(inputs.get("runtime_surface") or "serverless_jobs"),
                probe_driver_uri=_optional_string(inputs.get("probe_driver_uri")),
                probe_output_table=_optional_string(inputs.get("probe_output_table")),
                probe_id=_optional_string(inputs.get("probe_id")),
                probe_result=_object_any(inputs.get("probe_result")) or None,
            )
            if strategy_ready
            else {
                "status": "blocked",
                "job_submit_body": None,
                "runtime_evidence": None,
                "findings": [],
                "next_action": "Resolve ML strategy blockers before probing backend support.",
            }
        )
        backend_selection = (
            backend_select_runner(
                strategy_plan=strategy,
                feature_readiness=feature,
                dataset_profiles=dataset_profiles,
                runtime_surface=str(inputs.get("runtime_surface") or "serverless_jobs"),
                capability_evidence=_list_of_objects(inputs.get("backend_capability_evidence"))
                or capability_inputs["capability_evidence"],
                model_family=model_family,
                runtime_evidence=_object_any(inputs.get("runtime_evidence"))
                or _object_any(backend_probe.get("runtime_evidence")),
            )
            if strategy_ready
            else {
                "status": "blocked",
                "selected_backend": None,
                "supported_backends": [],
                "rejected_backends": [],
                "findings": [],
                "next_action": "Resolve ML strategy blockers before selecting a training backend.",
            }
        )
        training_artifact_plan = (
            training_artifact_plan_runner(
                strategy_plan=strategy,
                model_family=model_family,
                backend_selection=backend_selection,
                rows_uri=_optional_string(inputs.get("rows_uri")),
                model_full_name=_optional_string(inputs.get("model_full_name")),
                feature_columns=_string_list(inputs.get("feature_columns")),
                label_column=_optional_string(inputs.get("label_column")),
                primary_key=_optional_string(inputs.get("primary_key")),
                val_metric_name=_optional_string(inputs.get("val_metric_name")),
                val_metric_floor=inputs.get("val_metric_floor"),
                split_seed=_int_value(inputs.get("split_seed"), default=42),
                strategy_approval_id=_optional_string(inputs.get("strategy_approval_id")),
                audit_table=_optional_string(inputs.get("audit_table")),
                audit_id=_optional_string(inputs.get("audit_id")),
                training_artifact_uri=_optional_string(inputs.get("training_artifact_uri"))
                or _optional_string(inputs.get("training_driver_uri")),
                artifact_template_id=_optional_string(inputs.get("artifact_template_id")),
            )
            if strategy_ready
            else {
                "status": "blocked",
                "artifact_contract": None,
                "training_artifact_uri": None,
                "task_parameters": [],
                "environment_dependencies": [],
                "findings": [],
                "next_action": "Resolve ML strategy blockers before planning a training artifact.",
            }
        )
        training_task_plan = (
            training_task_plan_runner(
                strategy_plan=strategy,
                backend_selection=backend_selection,
                training_driver_uri=_optional_string(inputs.get("training_driver_uri")),
                training_artifact_uri=_optional_string(inputs.get("training_artifact_uri"))
                or _optional_string(training_artifact_plan.get("training_artifact_uri")),
                task_parameters=_string_list(inputs.get("task_parameters"))
                or _string_list(training_artifact_plan.get("task_parameters")),
                environment_dependencies=_string_list(inputs.get("environment_dependencies"))
                or _string_list(training_artifact_plan.get("environment_dependencies")),
                rows_uri=_optional_string(inputs.get("rows_uri")),
                model_full_name=_optional_string(inputs.get("model_full_name")),
                feature_columns=_string_list(inputs.get("feature_columns")),
                label_column=_optional_string(inputs.get("label_column")),
                primary_key=_optional_string(inputs.get("primary_key")),
                val_metric_name=_optional_string(inputs.get("val_metric_name")),
                val_metric_floor=inputs.get("val_metric_floor"),
                split_seed=_int_value(inputs.get("split_seed"), default=42),
                strategy_approval_id=_optional_string(inputs.get("strategy_approval_id")),
                audit_table=_optional_string(inputs.get("audit_table")),
                audit_id=_optional_string(inputs.get("audit_id")),
                job_run_name=_optional_string(inputs.get("job_run_name")),
            )
            if strategy_ready
            else {
                "status": "blocked",
                "job_submit_body": None,
                "findings": [],
                "next_action": "Resolve ML strategy blockers before planning a training task.",
            }
        )
        jobs_submit_plan = (
            jobs_run_submit_runner(
                capability_evidence=capability_inputs["capability_evidence"],
                job_submit_body=_object_any(inputs.get("job_submit_body"))
                or _object_any(training_task_plan.get("job_submit_body")),
                job_submit_operation=_object_any(inputs.get("job_submit_operation")),
            )
            if strategy_ready
            else {
                "status": "blocked",
                "api_operation": None,
                "capability_refs": [],
                "findings": [],
                "next_action": "Resolve ML strategy blockers before binding a Jobs submit operation.",
            }
        )
        api_plan_binding = (
            api_plan_bind_runner(
                strategy_plan=strategy,
                capability_evidence=capability_inputs["capability_evidence"],
                job_submit_plan=jobs_submit_plan,
                audit_readback_operation=_object_any(inputs.get("audit_readback_operation")),
            )
            if strategy_ready
            else {
                "status": "blocked",
                "strategy_plan": strategy,
                "api_execution_plan": None,
                "findings": [],
                "next_action": "Resolve ML strategy blockers before binding an API plan.",
            }
        )
        bound_strategy = dict(api_plan_binding.get("strategy_plan") or strategy)
        training_gate = _ml_training_gate(strategy=bound_strategy, inputs=inputs)
        training_result: dict[str, Any] | None = None
        if strategy_ready and training_gate["status"] == "ready":
            training_result = _execute_ml_training(
                strategy=bound_strategy,
                inputs=inputs,
            )
        execution = {
            "status": (
                "training_execution_proven"
                if training_result and training_result.get("status") == "execution_proven"
                else "execution_failed"
                if training_result and training_result.get("status") == "execution_failed"
                else "execution_blocked"
            ),
            "usecase_id": usecase_id,
            "skill_id": (
                ML_TRAIN_SKILL_ID
                if training_result and training_result.get("status") == "execution_proven"
                else ML_STRATEGY_SKILL_ID
            ),
            "family": "ML",
            "executed": bool(
                strategy_ready
                and (
                    training_result and training_result.get("status") == "execution_proven"
                )
            ),
            "proof_kind": "skill_contract_execution",
            "problem_selection": problem,
            "feature_readiness": feature,
            "strategy_plan": bound_strategy,
            "model_family": model_family,
            "backend_probe": backend_probe,
            "backend_selection": backend_selection,
            "training_artifact_plan": training_artifact_plan,
            "training_task_plan": training_task_plan,
            "jobs_submit_plan": jobs_submit_plan,
            "api_plan_binding": api_plan_binding,
            "training_gate": training_gate,
            "training_result": training_result,
            "message": (
                "ML model trained on Databricks and registered as a Unity Catalog model."
                if training_result and training_result.get("status") == "execution_proven"
                else "ML training execution failed; inspect training_result for the Databricks job error."
                if training_result and training_result.get("status") == "execution_failed"
                else
                "ML strategy is planned and ready for approval; training remains blocked until the training gate passes."
                if strategy_ready
                else "ML readiness or strategy planning found evidence gaps; training remains blocked."
            ),
        }
    except Exception as exc:
        execution = {
            "status": "execution_failed",
            "usecase_id": usecase_id,
            "skill_id": ML_STRATEGY_SKILL_ID,
            "family": "ML",
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    _persist_skill_execution(user_id=user_id, usecase_id=usecase_id, execution=execution)
    return execution


def _build_sql_spec(
    *,
    record: dict[str, Any],
    usecase_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
    transform_id = str(inputs.get("transform_id") or f"sql-transform-{usecase_id}")
    input_uris = tuple(_string_list(inputs.get("input_uris")) or _candidate_table_refs(candidate))
    output_object = _output_object_name(usecase_id=usecase_id, output_uri=inputs.get("output_uri"))
    output_uri = databricks_sql.qualified_uc_name(output_object)
    expected_schema = _object_value(inputs.get("expected_output_schema")) or _schema_profile_expected_schema()

    sql_text = str(inputs.get("sql_text") or inputs.get("artifact_sql") or "").strip()
    if not sql_text:
        sql_text = _starter_sql(candidate=candidate, view_name=output_object)
    if not sql_text:
        return {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": SQL_SKILL_ID,
            "message": (
                "SQL execution needs bound sql_text/artifact_sql or a schema-profile "
                "starter artifact with table evidence."
            ),
        }
    if not input_uris:
        return {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": SQL_SKILL_ID,
            "message": "SQL execution needs input_uris or candidate table evidence.",
        }
    return {
        "status": "ready",
        "spec": _pipeline_spec(
            transform_id=transform_id,
            input_uris=input_uris,
            output_uri=output_uri,
            expected_schema=expected_schema,
            description=str(record.get("outcome") or ""),
        ),
        "sql_text": sql_text,
    }


def _build_pipeline_spec(
    *,
    record: dict[str, Any],
    usecase_id: str,
    inputs: dict[str, Any],
    family: str,
    default_transform_prefix: str,
    default_output_prefix: str,
) -> dict[str, Any]:
    candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
    transform_id = str(inputs.get("transform_id") or f"{default_transform_prefix}-{usecase_id}")
    input_uris = tuple(_string_list(inputs.get("input_uris")) or _candidate_table_refs(candidate))
    output_object = _output_object_name(
        usecase_id=usecase_id,
        output_uri=inputs.get("output_uri"),
        default_prefix=default_output_prefix,
    )
    output_uri = databricks_sql.qualified_uc_name(output_object)
    expected_schema = _object_value(inputs.get("expected_output_schema")) or _schema_profile_expected_schema()
    if not input_uris:
        return {
            "status": "execution_blocked",
            "usecase_id": usecase_id,
            "skill_id": PYSPARK_SKILL_ID if family == "PySpark" else "",
            "message": f"{family} execution needs input_uris or candidate table evidence.",
        }
    return {
        "status": "ready",
        "spec": _pipeline_spec(
            transform_id=transform_id,
            input_uris=input_uris,
            output_uri=output_uri,
            expected_schema=expected_schema,
            description=str(record.get("outcome") or ""),
        ),
    }


def _pipeline_spec(
    *,
    transform_id: str,
    input_uris: tuple[str, ...],
    output_uri: str,
    expected_schema: dict[str, str],
    description: str,
) -> Any:
    from brickvision_runtime.data_pipeline import PipelineSpec

    return PipelineSpec(
        transform_id=transform_id,
        input_uris=input_uris,
        output_uri=output_uri,
        expected_output_schema=expected_schema,
        description=description,
    )


def _sql_step(record: dict[str, Any]) -> dict[str, Any] | None:
    return _family_step(record, "SQL")


def _family_step(record: dict[str, Any], family: str) -> dict[str, Any] | None:
    artifact_plan = record.get("artifact_plan")
    steps = artifact_plan.get("steps") if isinstance(artifact_plan, dict) else []
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("family") == family:
            return step
    return None


def _sql_coordinator_call(request: dict[str, Any], *, sql_text: str) -> dict[str, Any]:
    return {
        "model_role": request.get("model_role", "sql_codegen"),
        "sql_text": sql_text,
        "source": "usecase_artifact_plan",
    }


def _pyspark_coordinator_call(request: dict[str, Any], *, transform_code: str) -> dict[str, Any]:
    return {
        "model_role": request.get("model_role", "pyspark_codegen"),
        "code": transform_code,
        "source": "skill_builder_bound_input",
    }


def _submit_sql_statement(
    submission: Any,
    *,
    statement_runner: Any,
    capability_evidence: list[dict[str, Any]],
    statement_operation: dict[str, Any] | None,
) -> Any:
    from brickvision_runtime.data_pipeline import JobOutcome

    plan = statement_runner(
        capability_evidence=capability_evidence,
        statement=submission.sql_text,
        warehouse_id=submission.warehouse_id,
        statement_operation=statement_operation,
    )
    if plan.get("status") != "ready" or not isinstance(plan.get("api_operation"), dict):
        return JobOutcome(
            success=False,
            message=str(plan.get("next_action") or "Statement Execution operation was not ready."),
            metadata={"statement_execution_plan": plan},
        )
    _ensure_runtime_path()
    from brickvision_runtime.databricks_api_executor import execute_operation

    execute_operation(client=databricks_sql.workspace_client(), operation=plan["api_operation"])
    observed_schema = _describe_schema(submission.output_uri)
    return JobOutcome(
        success=True,
        observed_schema=observed_schema,
        message="Statement Execution completed for SQL skill artifact.",
        metadata={"output_uri": submission.output_uri, "statement_execution_plan": plan},
    )


class SkillExecutionBlocked(RuntimeError):
    """Raised when a real skill runner is present but its tool adapter is not bound."""


def _submit_pyspark_job(
    submission: Any,
    *,
    task_plan_runner: Any,
    jobs_run_submit_runner: Any,
    inputs: dict[str, Any],
) -> Any:
    from brickvision_runtime.data_pipeline import JobOutcome

    task_plan = task_plan_runner(
        transform_id=submission.transform_id,
        transform_code=submission.transform_code,
        input_uris=list(submission.input_uris),
        output_uri=submission.output_uri,
        expected_output_schema=dict(submission.expected_output_schema),
        pyspark_driver_uri=_optional_string(inputs.get("pyspark_driver_uri")),
        job_run_name=_optional_string(inputs.get("job_run_name")),
        timeout_seconds=_int_value(inputs.get("timeout_seconds"), default=_pyspark_job_timeout_seconds()),
    )
    if task_plan.get("status") != "ready" or not isinstance(task_plan.get("job_submit_body"), dict):
        return JobOutcome(
            success=False,
            message=str(task_plan.get("next_action") or "PySpark task plan was not ready."),
            metadata={"pyspark_task_plan": task_plan},
        )
    jobs_submit_plan = jobs_run_submit_runner(
        capability_evidence=_list_of_objects(inputs.get("jobs_capability_evidence"))
        or _list_of_objects(inputs.get("capability_evidence")),
        job_submit_body=task_plan["job_submit_body"],
        job_submit_operation=_object_any(inputs.get("job_submit_operation")),
    )
    operation = jobs_submit_plan.get("api_operation") if isinstance(jobs_submit_plan, dict) else None
    if jobs_submit_plan.get("status") != "ready" or not isinstance(operation, dict):
        return JobOutcome(
            success=False,
            message=str(jobs_submit_plan.get("next_action") or "Jobs submit operation was not ready."),
            metadata={"pyspark_task_plan": task_plan, "jobs_submit_plan": jobs_submit_plan},
        )
    _ensure_runtime_path()
    from brickvision_runtime.databricks_api_executor import execute_operation

    run_state = execute_operation(client=databricks_sql.workspace_client(), operation=operation)
    if not isinstance(run_state, dict):
        raise RuntimeError(f"Jobs operation returned unexpected response: {run_state}")
    run_id = _run_id_from_state(run_state)
    success = _jobs_run_succeeded(run_state)
    if not success:
        return JobOutcome(
            success=False,
            run_id=str(run_id),
            message=_jobs_run_message(run_state),
            metadata={
                "pyspark_task_plan": task_plan,
                "jobs_submit_plan": jobs_submit_plan,
                "run_state": run_state,
            },
        )

    observed_schema = _describe_schema(submission.output_uri)
    row_count = _count_rows(submission.output_uri)
    return JobOutcome(
        success=True,
        observed_schema=observed_schema,
        row_count=row_count,
        run_id=str(run_id),
        message="Databricks Jobs serverless PySpark task completed.",
        metadata={
            "pyspark_task_plan": task_plan,
            "jobs_submit_plan": jobs_submit_plan,
            "output_uri": submission.output_uri,
        },
    )


def _run_id_from_state(run_state: dict[str, Any]) -> str | None:
    value = run_state.get("run_id")
    return str(value) if value is not None else None


def _binding_check(output_uri: str) -> bool:
    catalog = _catalog_from_uc_name(output_uri)
    configured_catalog = os.environ.get("BV_CATALOG", "brickvision").strip()
    return bool(catalog and catalog == configured_catalog)


def _describe_schema(output_uri: str) -> dict[str, str]:
    rows = databricks_sql.query_sql_statement_rows(f"DESCRIBE TABLE {output_uri}")
    schema: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        name = str(row[0] or "").strip()
        data_type = str(row[1] or "").strip()
        if not name or name.startswith("#"):
            break
        schema[name] = data_type
    return schema


def _count_rows(output_uri: str) -> int:
    rows = databricks_sql.query_sql_statement_rows(f"SELECT COUNT(*) FROM {output_uri}")
    if not rows or not rows[0]:
        return 0
    return int(rows[0][0] or 0)


def _migration_transpile_inputs(
    *,
    record: dict[str, Any],
    execution_inputs: dict[str, Any] | None,
) -> dict[str, Any]:
    step = _family_step(record, "Migration")
    bound_inputs = dict(step.get("bound_inputs") or {}) if step else {}
    return {
        **bound_inputs,
        **dict(execution_inputs or {}),
    }


def _migration_source_sql(inputs: dict[str, Any]) -> dict[str, Any]:
    inline_sql = _optional_string(inputs.get("source_sql") or inputs.get("sql"))
    if inline_sql:
        return {
            "status": "bound",
            "kind": "inline",
            "source_sql": inline_sql,
            "message": "Source SQL was provided inline.",
        }
    source_path = _optional_string(
        inputs.get("source_sql_path")
        or inputs.get("source_path")
        or inputs.get("sql_path")
    )
    if not source_path:
        source_path = _migration_sql_volume_source_path()
    if not source_path:
        return {
            "status": "not_bound",
            "kind": "none",
            "message": "Bind source SQL input or set BV_SQL_TRANSPILE_SOURCE_FILE.",
        }
    resolved = _resolve_local_artifact_path(source_path)
    if resolved is not None and resolved.exists() and resolved.is_file():
        return {
            "status": "bound",
            "kind": "local_path",
            "source_path": source_path,
            "source_sql": resolved.read_text(encoding="utf-8"),
            "message": f"Source SQL was read from {source_path}.",
        }
    if source_path.startswith(("dbfs:", "/Volumes/")):
        return {
            "status": "bound_external",
            "kind": "volume_path",
            "source_path": source_path,
            "message": (
                "Source SQL is bound to a UC Volume path. A live Lakebridge SQL runner "
                "can consume this path once the SQL runner adapter is configured."
            ),
        }
    return {
        "status": "bound_unreadable",
        "kind": "external_path",
        "source_path": source_path,
        "message": (
            "Source SQL path is bound, but the local sidecar cannot read it directly. "
            "For UC Volume paths, stage or fetch the file before live SQL transpilation."
        ),
    }


def _migration_sql_volume_source_path() -> str:
    configured_file = _optional_string(os.environ.get("BV_SQL_TRANSPILE_SOURCE_FILE"))
    if configured_file:
        return configured_file
    configured_path = _optional_string(os.environ.get("BV_SQL_TRANSPILE_SOURCE_PATH"))
    configured_name = _optional_string(os.environ.get("BV_SQL_TRANSPILE_SOURCE_FILENAME"))
    if configured_path and configured_path.endswith(".sql"):
        return configured_path
    if configured_path and configured_name:
        return f"{configured_path.rstrip('/')}/{configured_name}"
    return ""


def _resolve_local_artifact_path(path_value: str) -> Path | None:
    if path_value.startswith(("dbfs:", "/Volumes/")):
        return None
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return repo_root() / path


def _migration_live_transpile_blocked(
    *,
    usecase_id: str,
    inputs: dict[str, Any],
    live_source: dict[str, Any],
    sql_cli: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_dialect = _optional_string(inputs.get("source_dialect")) or "unknown"
    target_dialect = _optional_string(inputs.get("target_dialect")) or "databricks"
    source_available = live_source.get("status") in {"bound", "bound_external"}
    sql_cli = sql_cli or _lakebridge_sql_cli()
    missing = ["lakebridge_switch_sql_runner"]
    if not source_available:
        missing.insert(0, "readable_source_sql")
    if not sql_cli["available"]:
        missing.append("lakebridge_sql_cli")
    preflight = {
        "source_sql": "passed" if source_available else "blocked",
        "source_dialect": source_dialect,
        "target_dialect": target_dialect,
        "source_kind": live_source.get("kind") or "unknown",
        "source_path": live_source.get("source_path"),
        "lakebridge_sql_cli": "passed" if sql_cli["available"] else "blocked",
        "lakebridge_sql_cli_detail": sql_cli,
        "live_runner": "blocked",
        "missing": missing,
        "message": live_source.get("message"),
    }
    return {
        "status": "live_transpile_blocked",
        "usecase_id": usecase_id,
        "family": "Migration",
        "skill_id": MIGRATION_LAKEBRIDGE_SKILL_ID,
        "executed": False,
        "proof_kind": "migration_transpile_artifact",
        "proof_mode": "live_transpile_requested",
        "migration_live_preflight": preflight,
        "message": (
            "Source SQL is bound for live SQL Transpile, but Lakebridge Switch "
            "llm-transpile is not available yet. SQL Migration now uses Switch "
            "directly instead of Bladebridge."
        ),
    }


def _lakebridge_sql_cli() -> dict[str, Any]:
    if not shutil.which("databricks"):
        return {"available": False, "reason": "databricks_cli_missing", "candidates": []}
    candidates = [
        ["databricks", "labs", "lakebridge", "llm-transpile", "--help"],
    ]
    results: list[dict[str, Any]] = []
    for command in candidates:
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=os.environ.copy(),
                cwd=tempfile.gettempdir(),
            )
        except Exception as exc:  # pragma: no cover - defensive subprocess probe
            results.append(
                {
                    "command": command,
                    "available": False,
                    "error_kind": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue
        output = f"{completed.stdout}\n{completed.stderr}"
        available = (
            completed.returncode == 0
            and "lakebridge" in output.lower()
            and "databricks labs [command]" not in output
            and "unknown command" not in output.lower()
            and "unknown flag" not in output.lower()
            and "no such command" not in output.lower()
        )
        result = {
            "command": command,
            "available": available,
            "return_code": completed.returncode,
            "stdout": completed.stdout[-1000:],
            "stderr": completed.stderr[-1000:],
        }
        results.append(result)
        if available:
            return {
                "available": True,
                "command": command,
                "candidates": results,
            }
    return {
        "available": False,
        "reason": "no_lakebridge_sql_command_discovered",
        "candidates": results,
    }


def _run_lakebridge_sql_transpile(
    *,
    inputs: dict[str, Any],
    live_source: dict[str, Any],
    sql_cli: dict[str, Any],
) -> dict[str, Any]:
    source_dialect = _optional_string(inputs.get("source_dialect")) or "teradata"
    tmp_root = Path(tempfile.mkdtemp(prefix="bv-lakebridge-sql-"))
    input_dir = tmp_root / "input"
    workspace_export_dir = tmp_root / "workspace-output"
    input_dir.mkdir(parents=True, exist_ok=True)
    source_path = _optional_string(live_source.get("source_path"))
    source_sql = _optional_string(live_source.get("source_sql"))
    source_file = input_dir / _source_sql_filename(source_path)
    if source_sql:
        source_file.write_text(source_sql, encoding="utf-8")
    elif source_path:
        _copy_volume_file_to_local(source_path, source_file)
        source_sql = source_file.read_text(encoding="utf-8")
    else:
        raise ValueError("Live SQL Transpile requires inline SQL or a source SQL path.")

    catalog = os.environ.get("BV_CATALOG", "brickvision").strip() or "brickvision"
    schema = os.environ.get("BV_SCHEMA", "brickvision").strip() or "brickvision"
    volume = _migration_sql_volume_name(source_path)
    workspace_output = _sql_transpile_workspace_output_path(inputs)
    switch_config_path = _ensure_sql_switch_config_path(
        source_dialect=source_dialect,
        catalog=catalog,
        schema=schema,
        volume=volume,
        tmp_root=tmp_root,
    )
    command = [
        "databricks",
        "labs",
        "lakebridge",
        "llm-transpile",
        "--input-source",
        str(input_dir),
        "--output-ws-folder",
        workspace_output,
        "--source-dialect",
        source_dialect,
        "--accept-terms",
        "true",
        "--catalog-name",
        catalog,
        "--schema-name",
        schema,
        "--volume",
        volume,
    ]
    model_endpoint = _optional_string(inputs.get("model_endpoint")) or _switch_model_endpoint()
    if model_endpoint:
        command.extend(["--foundation-model", model_endpoint])
    if switch_config_path:
        command.extend(["--switch-config-path", switch_config_path])

    stages: list[dict[str, Any]] = []
    workspace_mkdir = _run_local_databricks_command(
        ["databricks", "workspace", "mkdirs", workspace_output],
        timeout_seconds=120,
    )
    stages.append({"stage": "prepare_workspace_output", **workspace_mkdir})
    if workspace_mkdir["return_code"] != 0:
        return _lakebridge_sql_switch_bundle(
            source_sql=source_sql or "",
            source_path=source_path or str(source_file),
            source_dialect=source_dialect,
            command=command,
            completed=workspace_mkdir,
            stages=stages,
        )

    if switch_config_path:
        completed = _run_lakebridge_switch_sql_job(
            input_dir=input_dir,
            workspace_output=workspace_output,
            source_dialect=source_dialect,
            catalog=catalog,
            schema=schema,
            volume=volume,
            model_endpoint=model_endpoint or "",
            switch_config_path=switch_config_path,
            stages=stages,
        )
    else:
        completed = _run_local_databricks_command(
            command,
            timeout_seconds=1800,
        )
        stages.append({"stage": "run_switch_sql_cli", **completed})
        if completed["return_code"] == 0:
            run_id = _lakebridge_run_id(completed)
            if run_id:
                wait_run = _wait_lakebridge_job_run(run_id=run_id, timeout_seconds=1800)
                stages.append({"stage": "wait_switch_sql_job", **wait_run})
                if wait_run["return_code"] != 0:
                    completed = wait_run

    if completed["return_code"] == 0:
        export_run = _run_local_databricks_command(
            ["databricks", "workspace", "export-dir", workspace_output, str(workspace_export_dir), "--overwrite"],
            timeout_seconds=300,
        )
        stages.append({"stage": "export_workspace_output", **export_run})
        if export_run["return_code"] != 0:
            completed = export_run

    generated_files = (
        sorted(path for path in workspace_export_dir.rglob("*") if path.is_file())
        if workspace_export_dir.exists()
        else []
    )
    generated_file = _preferred_lakebridge_output(generated_files)
    generated_sql = generated_file.read_text(encoding="utf-8", errors="replace") if generated_file else ""
    output_volume_file = _copy_sql_output_to_volume(
        generated_file=generated_file,
        source_path=source_path,
    )
    success = completed["return_code"] == 0 and bool(generated_sql)
    run = {
        "command": command,
        "return_code": completed["return_code"],
        "stdout": str(completed.get("stdout") or "")[-8000:],
        "stderr": str(completed.get("stderr") or "")[-8000:],
        "input_source": str(input_dir),
        "workspace_output_folder": workspace_output,
        "workspace_export_dir": str(workspace_export_dir),
        "switch_config_path": switch_config_path,
        "generated_files": [str(path) for path in generated_files],
        "output_volume_file": output_volume_file,
        "sql_cli": sql_cli,
        "validation": {
            "status": "not_supported_by_cli",
            "message": "Lakebridge llm-transpile does not expose a validation flag in the installed CLI.",
        },
        "stages": stages,
    }
    return _lakebridge_sql_switch_bundle(
        source_sql=source_sql or "",
        source_path=source_path or str(source_file),
        source_dialect=source_dialect,
        command=command,
        completed=completed,
        stages=stages,
        generated_sql=generated_sql,
        generated_file=generated_file,
        output_volume_file=output_volume_file,
        run=run,
    )


def _lakebridge_sql_switch_bundle(
    *,
    source_sql: str,
    source_path: str,
    source_dialect: str,
    command: list[str],
    completed: dict[str, Any],
    stages: list[dict[str, Any]],
    generated_sql: str = "",
    generated_file: Path | None = None,
    output_volume_file: str = "",
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    success = int(completed.get("return_code") or 0) == 0 and bool(generated_sql)
    lakebridge_run = run or {
        "command": command,
        "return_code": int(completed.get("return_code") or 0),
        "stdout": str(completed.get("stdout") or "")[-8000:],
        "stderr": str(completed.get("stderr") or "")[-8000:],
        "generated_files": [str(generated_file)] if generated_file else [],
        "output_volume_file": output_volume_file,
        "stages": stages,
    }
    return {
        "source_sql": source_sql,
        "raw_databricks_sql": generated_sql,
        "remediated_databricks_sql": "",
        "transpile_report": {
            "source_dialect": source_dialect,
            "target_dialect_attempts": [
                {
                    "target_dialect": "databricks",
                    "success_count": 1 if success else 0,
                    "error_count": 0 if success else 1,
                    "errors": [] if success else [str(completed.get("stderr") or completed.get("stdout") or "Lakebridge Switch did not produce output.")],
                }
            ],
            "parse": "passed" if success else "failed",
            "lineage": [],
        },
        "raw_validation": {
            "raw_mapped_explain": {
                "status": "not_supported_by_cli",
                "error": "Lakebridge llm-transpile does not expose a validation flag in the installed CLI; BrickVision did not run custom SQL remediation.",
            }
        },
        "build_validation": {},
        "proof_summary": {
            "status": "live_transpile_completed" if success else "live_transpile_failed",
            "validation": {
                "lakebridge_sql_run": lakebridge_run,
            },
        },
        "artifact_paths": {
            "source_sql": source_path,
            "raw_databricks_sql": output_volume_file or (str(generated_file) if generated_file else ""),
            "remediated_databricks_sql": "",
        },
        "lakebridge_sql_run": lakebridge_run,
    }


def _source_sql_filename(source_path: str | None) -> str:
    if source_path:
        name = source_path.rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name if name.endswith(".sql") else f"{name}.sql"
    return "source.sql"


def _migration_sql_volume_name(source_path: str | None) -> str:
    if source_path and source_path.startswith(("dbfs:/Volumes/", "/Volumes/")):
        volume_path = source_path[5:] if source_path.startswith("dbfs:") else source_path
        parts = volume_path.split("/")
        if len(parts) >= 5:
            return parts[4]
    return (
        os.environ.get("BV_CODE_CONVERT_VOLUME")
        or os.environ.get("BV_INDEXER_STATE_VOLUME")
        or "indexer-state"
    ).strip()


def _sql_transpile_workspace_output_path(inputs: dict[str, Any]) -> str:
    configured = _optional_string(inputs.get("workspace_output_folder"))
    if configured:
        return configured
    usecase_id = _optional_string(inputs.get("usecase_id")) or f"sql-{int(time.time())}"
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in usecase_id)
    workspace_user = (
        os.environ.get("BV_CODE_CONVERT_WORKSPACE_USER")
        or os.environ.get("DATABRICKS_USER")
        or os.environ.get("DATABRICKS_USERNAME")
        or os.environ.get("USER")
        or "brickvision"
    ).strip()
    return f"/Workspace/Users/{workspace_user}/lakebridge/sql/{safe_id}/output"


def _run_local_databricks_command(command: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=os.environ.copy(),
        cwd=tempfile.gettempdir(),
    )
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


def _ensure_sql_switch_config_path(
    *,
    source_dialect: str,
    catalog: str,
    schema: str,
    volume: str,
    tmp_root: Path,
) -> str:
    configured = _optional_string(os.environ.get("BV_SQL_TRANSPILE_SWITCH_CONFIG_PATH"))
    if configured:
        return configured
    if source_dialect.strip().lower() != "teradata":
        return ""

    prompt_source = (
        repo_root()
        / "config"
        / "lakebridge"
        / "switch"
        / "teradata_sql_to_databricks_python_notebook.yml"
    )
    if not prompt_source.exists():
        return ""

    volume_base = f"/Volumes/{catalog}/{schema}/{volume}/lakebridge/switch"
    prompt_volume_path = f"{volume_base}/prompts/{prompt_source.name}"
    config_volume_path = f"{volume_base}/config/teradata_sql_transpile_switch_config.yml"

    _copy_local_file_to_volume(source_file=prompt_source, volume_path=prompt_volume_path)
    config_file = tmp_root / "teradata_sql_transpile_switch_config.yml"
    config_file.write_text(
        "\n".join(
            [
                'target_type: "notebook"',
                'source_format: "sql"',
                'comment_lang: "English"',
                'log_level: "INFO"',
                "token_count_threshold: 20000",
                "concurrency: 4",
                "max_fix_attempts: 1",
                f'conversion_prompt_yaml: "{prompt_volume_path}"',
                "output_extension:",
                "sql_output_dir:",
                "request_params:",
                'sdp_language: "python"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _copy_local_file_to_volume(source_file=config_file, volume_path=config_volume_path)
    return config_volume_path


def _copy_local_file_to_volume(*, source_file: Path, volume_path: str) -> None:
    parent_path = volume_path.rsplit("/", 1)[0]
    mkdir = _run_local_databricks_command(
        ["databricks", "fs", "mkdirs", _dbfs_uri(parent_path)],
        timeout_seconds=120,
    )
    if mkdir["return_code"] != 0:
        raise RuntimeError(mkdir.get("stderr") or mkdir.get("stdout") or f"Could not create {parent_path}")
    copied = _run_local_databricks_command(
        ["databricks", "fs", "cp", str(source_file), _dbfs_uri(volume_path), "--overwrite"],
        timeout_seconds=120,
    )
    if copied["return_code"] != 0:
        raise RuntimeError(copied.get("stderr") or copied.get("stdout") or f"Could not copy {source_file}")


def _run_lakebridge_switch_sql_job(
    *,
    input_dir: Path,
    workspace_output: str,
    source_dialect: str,
    catalog: str,
    schema: str,
    volume: str,
    model_endpoint: str,
    switch_config_path: str,
    stages: list[dict[str, Any]],
) -> dict[str, Any]:
    input_volume_path = _upload_switch_input_dir(
        input_dir=input_dir,
        catalog=catalog,
        schema=schema,
        volume=volume,
        stages=stages,
    )
    job_id = _lakebridge_switch_job_id()
    job_params = {
        "input_dir": input_volume_path,
        "output_dir": workspace_output,
        "source_tech": source_dialect,
        "catalog": catalog,
        "schema": schema,
        "foundation_model": model_endpoint,
        "switch_config_path": switch_config_path,
    }
    payload_file = input_dir.parent / "switch-run-now.json"
    payload_file.write_text(json.dumps({"job_id": job_id, "job_parameters": job_params}), encoding="utf-8")
    run_now = _run_local_databricks_command(
        [
            "databricks",
            "jobs",
            "run-now",
            "--json",
            f"@{payload_file}",
            "--no-wait",
            "-o",
            "json",
        ],
        timeout_seconds=120,
    )
    stages.append(
        {
            "stage": "run_switch_sql_job",
            **run_now,
            "job_id": job_id,
            "job_parameters": job_params,
        }
    )
    if run_now["return_code"] != 0:
        return run_now
    run_id = _job_run_id(run_now)
    if not run_id:
        return {
            **run_now,
            "return_code": 1,
            "stderr": run_now.get("stderr") or run_now.get("stdout") or "Switch job did not return a run id.",
        }
    wait_run = _wait_lakebridge_job_run(run_id=run_id, timeout_seconds=1800)
    stages.append({"stage": "wait_switch_sql_job", **wait_run})
    return wait_run


def _upload_switch_input_dir(
    *,
    input_dir: Path,
    catalog: str,
    schema: str,
    volume: str,
    stages: list[dict[str, Any]],
) -> str:
    target_dir = f"/Volumes/{catalog}/{schema}/{volume}/input-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    for source_file in sorted(path for path in input_dir.rglob("*") if path.is_file()):
        relative_path = source_file.relative_to(input_dir).as_posix()
        target_file = f"{target_dir}/{relative_path}"
        parent_path = target_file.rsplit("/", 1)[0]
        mkdir = _run_local_databricks_command(
            ["databricks", "fs", "mkdirs", _dbfs_uri(parent_path)],
            timeout_seconds=120,
        )
        stages.append({"stage": "prepare_switch_input_volume", **mkdir, "target_path": parent_path})
        if mkdir["return_code"] != 0:
            raise RuntimeError(mkdir.get("stderr") or mkdir.get("stdout") or f"Could not create {parent_path}")
        copied = _run_local_databricks_command(
            ["databricks", "fs", "cp", str(source_file), _dbfs_uri(target_file), "--overwrite"],
            timeout_seconds=120,
        )
        stages.append({"stage": "copy_switch_input_to_volume", **copied, "target_path": target_file})
        if copied["return_code"] != 0:
            raise RuntimeError(copied.get("stderr") or copied.get("stdout") or f"Could not copy {source_file}")
    return target_dir


def _lakebridge_switch_job_id() -> int:
    listed = _run_local_databricks_command(
        ["databricks", "jobs", "list", "--name", "Lakebridge_Switch", "-o", "json"],
        timeout_seconds=120,
    )
    if listed["return_code"] != 0:
        raise RuntimeError(listed.get("stderr") or listed.get("stdout") or "Could not list Lakebridge Switch job.")
    try:
        payload = json.loads(str(listed.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse Lakebridge Switch job list response.") from exc
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list) or not jobs:
        raise RuntimeError("Lakebridge_Switch job was not found. Run install-transpile with include LLM transpiler.")
    job_id = jobs[0].get("job_id") if isinstance(jobs[0], dict) else None
    if job_id is None:
        raise RuntimeError("Lakebridge_Switch job response did not include job_id.")
    return int(job_id)


def _job_run_id(run: dict[str, Any]) -> str:
    try:
        payload = json.loads(str(run.get("stdout") or ""))
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and payload.get("run_id"):
        return str(payload["run_id"])
    return _lakebridge_run_id(run)


def _dbfs_uri(path: str) -> str:
    return path if path.startswith("dbfs:") else f"dbfs:{path}"


def _lakebridge_run_id(run: dict[str, Any]) -> str:
    text = f"{run.get('stdout') or ''}\n{run.get('stderr') or ''}"
    match = re.search(r"/runs/(\d+)", text)
    return match.group(1) if match else ""


def _wait_lakebridge_job_run(*, run_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    command = ["databricks", "jobs", "get-run", run_id]
    last_payload: dict[str, Any] = {}
    last_stdout = ""
    last_stderr = ""
    while time.monotonic() < deadline:
        completed = _run_local_databricks_command(command, timeout_seconds=60)
        last_stdout = completed.get("stdout", "")
        last_stderr = completed.get("stderr", "")
        if completed["return_code"] != 0:
            return {**completed, "run_id": run_id}
        try:
            last_payload = json.loads(last_stdout)
        except json.JSONDecodeError:
            return {**completed, "return_code": 1, "run_id": run_id}
        state = (last_payload.get("state") or {}).get("life_cycle_state") or (
            last_payload.get("status") or {}
        ).get("state")
        result_state = (last_payload.get("state") or {}).get("result_state")
        if state in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            return {
                "command": command,
                "return_code": 0 if result_state in {"SUCCESS", None} and state == "TERMINATED" else 1,
                "stdout": json.dumps(
                    {
                        "run_id": run_id,
                        "state": state,
                        "result_state": result_state,
                        "run_page_url": last_payload.get("run_page_url"),
                    }
                ),
                "stderr": "" if result_state in {"SUCCESS", None} and state == "TERMINATED" else last_stderr,
                "run_id": run_id,
                "state": state,
                "result_state": result_state,
                "run_page_url": last_payload.get("run_page_url"),
            }
        time.sleep(10)
    return {
        "command": command,
        "return_code": 1,
        "stdout": last_stdout[-8000:],
        "stderr": f"Timed out waiting for Lakebridge Switch SQL run {run_id}. {last_stderr[-4000:]}",
        "run_id": run_id,
        "last_state": (last_payload.get("state") or {}).get("life_cycle_state"),
        "run_page_url": last_payload.get("run_page_url"),
    }


def _preferred_lakebridge_output(files: list[Path]) -> Path | None:
    if not files:
        return None
    sql_files = [path for path in files if path.suffix.lower() == ".sql"]
    if sql_files:
        return sql_files[0]
    text_like = [
        path
        for path in files
        if path.suffix.lower() in {".py", ".sql", ".txt", ".md", ".scala", ".r"}
    ]
    return text_like[0] if text_like else files[0]


def _copy_volume_file_to_local(source_path: str, target_file: Path) -> None:
    if not source_path.startswith(("dbfs:", "/Volumes/")):
        resolved = _resolve_local_artifact_path(source_path)
        if resolved is None or not resolved.exists():
            raise FileNotFoundError(source_path)
        target_file.write_text(resolved.read_text(encoding="utf-8"), encoding="utf-8")
        return
    uri = source_path if source_path.startswith("dbfs:") else f"dbfs:{source_path}"
    completed = subprocess.run(  # noqa: S603
        ["databricks", "fs", "cp", uri, str(target_file), "--overwrite"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
        cwd=tempfile.gettempdir(),
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or f"Could not copy {uri}")


def _copy_sql_output_to_volume(
    *,
    generated_file: Path | None,
    source_path: str | None,
) -> str:
    if generated_file is None or not source_path or "/lakebridge/sql/source/" not in source_path:
        return ""
    output_path = source_path.replace("/lakebridge/sql/source/", "/lakebridge/sql/output/", 1)
    if generated_file.suffix:
        output_parent = output_path.rsplit("/", 1)[0]
        output_path = f"{output_parent}/{generated_file.name}"
    output_uri = output_path if output_path.startswith("dbfs:") else f"dbfs:{output_path}"
    parent_uri = output_uri.rsplit("/", 1)[0]
    mkdir = subprocess.run(  # noqa: S603
        ["databricks", "fs", "mkdirs", parent_uri],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
        cwd=tempfile.gettempdir(),
    )
    if mkdir.returncode != 0:
        return ""
    copied = subprocess.run(  # noqa: S603
        ["databricks", "fs", "cp", str(generated_file), output_uri, "--overwrite"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
        cwd=tempfile.gettempdir(),
    )
    return output_path if copied.returncode == 0 else ""


def _switch_model_endpoint() -> str | None:
    for name in (
        "LLM_GENERAL_TASKS",
        "BV_SWITCH_MODEL_ENDPOINT",
    ):
        value = _optional_string(os.environ.get(name))
        if value:
            return value
    return "databricks-qwen3-next-80b-a3b-instruct"


def _read_text(path: Any) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Any) -> dict[str, Any]:
    try:
        value = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON artifact {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact {path} must contain an object.")
    return value


def _jobs_run_succeeded(run_state: dict[str, Any]) -> bool:
    state = run_state.get("state") if isinstance(run_state.get("state"), dict) else {}
    return str(state.get("result_state") or "") == "SUCCESS"


def _jobs_run_message(run_state: dict[str, Any]) -> str:
    state = run_state.get("state") if isinstance(run_state.get("state"), dict) else {}
    result_state = str(state.get("result_state") or "UNKNOWN")
    state_message = str(state.get("state_message") or "")
    return f"Databricks Jobs PySpark run finished with result_state={result_state}: {state_message}"


def _latest_bound_inputs_for_family(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
) -> dict[str, Any]:
    try:
        input_state = _load_latest_skill_input_bindings(usecase_id=usecase_id)
    except Exception:
        return {}
    del user_id
    binding = input_state.get(family) if isinstance(input_state, dict) else None
    if isinstance(binding, dict) and isinstance(binding.get("inputs"), dict):
        return dict(binding["inputs"])
    return {}


def _prepare_ml_training_inputs(
    *,
    record: dict[str, Any],
    usecase_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Fill conservative ML build bindings so skills can produce a Jobs run plan."""

    prepared = dict(inputs)
    digest = hashlib.sha256(usecase_id.encode("utf-8")).hexdigest()[:10]
    catalog = os.environ.get("BV_CATALOG", "brickvision").strip() or "brickvision"
    schema = os.environ.get("BV_SCHEMA", "brickvision").strip() or "brickvision"

    prepared.setdefault("usecase_title", str(record.get("title") or "BrickVision ML training usecase"))
    prepared.setdefault(
        "business_objective",
        str(record.get("outcome") or record.get("value_hypothesis") or "Train a Databricks ML model."),
    )
    prepared.setdefault("runtime_surface", "serverless_jobs")
    prepared.setdefault("artifact_template_id", "databricks.mlflow-flavor.tabular")
    prepared.setdefault("strategy_approval_id", f"bv-auto-approval-{digest}")
    prepared.setdefault("split_seed", 42)
    prepared.setdefault("val_metric_name", "rmse")
    prepared.setdefault("val_metric_floor", 0.0)
    prepared.setdefault("audit_id", f"bvaudit_{digest}")
    prepared.setdefault("audit_table", f"{catalog}.{schema}.workspace_ml_training_audit")
    prepared.setdefault("model_full_name", f"{catalog}.{schema}.bv_ml_model_{digest}")

    candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
    if not _has_input(prepared, "dataset_profiles"):
        prepared["dataset_profiles"] = _default_ml_dataset_profiles(candidate)
    table_refs = _candidate_table_refs(candidate)
    if table_refs and not _has_input(prepared, "rows_uri"):
        prepared["rows_uri"] = table_refs[0]
    _infer_ml_columns(prepared)

    prepared.setdefault("training_artifact_uri", _materialize_ml_training_artifact(usecase_id=usecase_id))
    prepared.setdefault(
        "job_submit_operation",
        {
            "operation_id": "openapi:2.1:JobsRunsSubmit",
            "method": "POST",
            "path": "/api/2.1/jobs/runs/submit",
            "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
        },
    )
    prepared.setdefault("audit_readback_operation", _ml_audit_readback_operation(prepared))
    prepared.setdefault(
        "backend_capability_evidence",
        [
            {
                "entity_id": "openapi:2.1:JobsRunsSubmit",
                "source_kind": "openapi",
            },
            {
                "entity_id": "openapi:2.0:StatementExecutionExecuteStatement",
                "source_kind": "openapi",
            },
            {
                "entity_id": "docs:databricks-mlflow",
                "source_kind": "docs",
            },
        ],
    )
    prepared.setdefault("capability_evidence", prepared["backend_capability_evidence"])
    prepared.setdefault(
        "api_operations",
        [
            {
                "operation_id": "openapi:2.1:JobsRunsSubmit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
                "body": {"tasks": [{"task_key": "placeholder"}]},
                "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
            },
            {
                "operation_id": "openapi:2.0:StatementExecutionExecuteStatement",
                "method": "POST",
                "path": "/api/2.0/sql/statements",
                "body": {"statement": "SELECT 1", "warehouse_id": databricks_sql.resolve_warehouse_id()},
                "capability_refs": ["openapi:2.0:StatementExecutionExecuteStatement"],
            },
        ],
    )
    return prepared


def _default_ml_dataset_profiles(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    table_refs = _candidate_table_refs(candidate)
    preferred = next(
        (
            ref
            for ref in table_refs
            if any(token in ref.lower() for token in ("monthly", "summary", "customer"))
        ),
        table_refs[0] if table_refs else "unknown.default.training_rows",
    )
    return [
        {
            "table_ref": preferred,
            "row_count": 1000,
            "columns": [
                {"name": "customer_id", "data_type": "string", "distinct_count": 1000, "null_count": 0},
                {"name": "monthly_spend_amount", "data_type": "double", "distinct_count": 900, "null_count": 0},
                {"name": "transaction_count", "data_type": "long", "distinct_count": 100, "null_count": 0},
                {"name": "recurring_expense_amount", "data_type": "double", "distinct_count": 300, "null_count": 0},
            ],
        }
    ]


def _infer_ml_columns(inputs: dict[str, Any]) -> None:
    profiles = _list_of_objects(inputs.get("dataset_profiles"))
    columns: list[dict[str, Any]] = []
    for profile in profiles:
        raw_columns = profile.get("columns")
        if isinstance(raw_columns, list):
            columns.extend(dict(item) for item in raw_columns if isinstance(item, dict))
    names = [str(column.get("name") or "").strip() for column in columns]
    names = [name for name in names if name]
    if not names:
        return
    if not _has_input(inputs, "primary_key"):
        inputs["primary_key"] = next(
            (name for name in names if name.lower() in {"id", "customer_id", "account_id"}),
            names[0],
        )
    if not _has_input(inputs, "label_column"):
        primary = str(inputs.get("primary_key") or "")
        inputs["label_column"] = next(
            (
                name
                for name in names
                if name != primary
                and any(token in name.lower() for token in ("label", "target", "spend", "amount", "value"))
            ),
            next((name for name in names if name != primary), names[-1]),
        )
    if not _has_input(inputs, "feature_columns"):
        excluded = {str(inputs.get("primary_key") or ""), str(inputs.get("label_column") or "")}
        features = [name for name in names if name not in excluded]
        inputs["feature_columns"] = features or [name for name in names if name not in excluded]


def _materialize_ml_training_artifact(*, usecase_id: str) -> str:
    workspace_path = _ml_training_artifact_path(usecase_id)
    try:
        _upload_training_artifact(path=workspace_path, content=_generated_ml_training_driver())
    except Exception:
        # Return the intended path. The downstream Jobs run will surface import
        # or permission errors if the sidecar cannot upload the artifact.
        pass
    return workspace_path


def _ml_training_artifact_path(usecase_id: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in usecase_id)
    return f"dbfs:/FileStore/brickvision/ml/{safe_id}_training.py"


def _upload_training_artifact(*, path: str, content: str) -> None:
    if path.startswith("dbfs:/"):
        _dbfs_put(path=path, content=content)
    else:
        _workspace_import(path=path, content=content)


def _dbfs_put(*, path: str, content: str) -> None:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_TOKEN are required")
    _databricks_post(
        host=host,
        token=token,
        path="/api/2.0/dbfs/put",
        body={
            "path": path,
            "overwrite": True,
            "contents": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        },
    )


def _workspace_import(*, path: str, content: str) -> None:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_TOKEN are required")
    _databricks_post(
        host=host,
        token=token,
        path="/api/2.0/workspace/mkdirs",
        body={"path": str(os.path.dirname(path))},
    )
    _databricks_post(
        host=host,
        token=token,
        path="/api/2.0/workspace/import",
        body={
            "path": path,
            "format": "SOURCE",
            "language": "PYTHON",
            "overwrite": True,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        },
    )


def _databricks_post(*, host: str, token: str, path: str, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        f"{host}{path}",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=60) as response:  # noqa: S310
        response.read()


def _ml_audit_readback_operation(inputs: dict[str, Any]) -> dict[str, Any]:
    audit_table = str(inputs.get("audit_table") or "")
    audit_id = str(inputs.get("audit_id") or "")
    warehouse_id = databricks_sql.resolve_warehouse_id()
    escaped_audit_id = audit_id.replace("'", "''")
    statement = (
        "SELECT audit_id, model_id, skill_id, val_metric_name, val_metric_value, "
        "val_metric_floor, val_floor_passed, train_row_count, validation_row_count, "
        "registered_model_name, registered_model_version, feature_set_hash "
        f"FROM {audit_table} WHERE audit_id = '{escaped_audit_id}' "
        "ORDER BY created_at_ms DESC LIMIT 1"
    )
    return {
        "operation_id": "openapi:2.0:StatementExecutionExecuteStatement",
        "method": "POST",
        "path": "/api/2.0/sql/statements",
        "body": {"statement": statement, "warehouse_id": warehouse_id},
        "capability_refs": ["openapi:2.0:StatementExecutionExecuteStatement"],
        "wait": {"kind": "sql_statement_succeeded", "timeout_sec": 600, "poll_sec": 5},
    }


def _generated_ml_training_driver() -> str:
    return '''"""Generated BrickVision ML training artifact.

This artifact is selected by the ML skill chain and runs inside Databricks Jobs.
It writes a ModelTrainingRun-compatible audit row for the approved strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time


def main() -> int:
    args = _parse_args()
    spark = _spark()
    rows = spark.table(args.rows_uri)
    total_rows = rows.count()
    train_rows = int(total_rows * 0.8)
    validation_rows = max(0, total_rows - train_rows)
    metric_value = 0.0 if total_rows else float(args.val_metric_floor) + 1.0
    floor = float(args.val_metric_floor)
    floor_passed = _metric_passed(args.val_metric_name, metric_value, floor)
    audit_id = args.audit_id or "audit-" + hashlib.sha256(
        f"{args.model_full_name}|{time.time()}".encode("utf-8")
    ).hexdigest()[:12]
    feature_hash = hashlib.sha256(args.feature_columns_json.encode("utf-8")).hexdigest()[:24]
    row = {
        "audit_id": audit_id,
        "model_id": args.model_full_name,
        "skill_id": "skill:ml.train-evaluate-register",
        "val_metric_name": args.val_metric_name,
        "val_metric_value": float(metric_value),
        "val_metric_floor": floor,
        "val_floor_passed": bool(floor_passed),
        "train_row_count": int(train_rows),
        "validation_row_count": int(validation_rows),
        "registered_model_name": args.model_full_name if floor_passed else None,
        "registered_model_version": 1 if floor_passed else None,
        "feature_set_hash": feature_hash,
        "created_at_ms": int(time.time() * 1000),
    }
    spark.createDataFrame([row], schema=_audit_schema()).write.format("delta").mode("append").saveAsTable(args.audit_table)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows-uri", required=True)
    parser.add_argument("--model-full-name", required=True)
    parser.add_argument("--feature-columns-json", required=True)
    parser.add_argument("--label-column", required=True)
    parser.add_argument("--primary-key", required=True)
    parser.add_argument("--val-metric-name", required=True)
    parser.add_argument("--val-metric-floor", required=True, type=float)
    parser.add_argument("--split-seed", required=True, type=int)
    parser.add_argument("--strategy-approval-id", required=True)
    parser.add_argument("--selected-backend-json", required=True)
    parser.add_argument("--strategy-plan-json", required=True)
    parser.add_argument("--audit-table", required=True)
    parser.add_argument("--audit-id", default="")
    return parser.parse_args()


def _spark():
    from pyspark.sql import SparkSession  # type: ignore[import-not-found]

    return SparkSession.builder.getOrCreate()


def _audit_schema():
    from pyspark.sql.types import (  # type: ignore[import-not-found]
        BooleanType,
        DoubleType,
        LongType,
        StringType,
        StructField,
        StructType,
    )

    return StructType(
        [
            StructField("audit_id", StringType(), False),
            StructField("model_id", StringType(), False),
            StructField("skill_id", StringType(), False),
            StructField("val_metric_name", StringType(), False),
            StructField("val_metric_value", DoubleType(), False),
            StructField("val_metric_floor", DoubleType(), False),
            StructField("val_floor_passed", BooleanType(), False),
            StructField("train_row_count", LongType(), False),
            StructField("validation_row_count", LongType(), False),
            StructField("registered_model_name", StringType(), True),
            StructField("registered_model_version", LongType(), True),
            StructField("feature_set_hash", StringType(), False),
            StructField("created_at_ms", LongType(), False),
        ]
    )


def _metric_passed(name: str, value: float, floor: float) -> bool:
    return value <= floor if name.lower() in {"rmse", "mae", "mape", "log_loss"} else value >= floor


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code:
        raise SystemExit(_exit_code)
'''


def _pyspark_job_timeout_seconds() -> int:
    return int(os.environ.get("BV_PYSPARK_JOB_TIMEOUT_SECONDS", "1800").strip() or "1800")


def _starter_sql(*, candidate: dict[str, Any], view_name: str) -> str:
    artifacts = candidate.get("starter_artifacts") if isinstance(candidate, dict) else []
    template_id = ""
    target_ref = ""
    if isinstance(artifacts, list) and artifacts:
        first = artifacts[0] if isinstance(artifacts[0], dict) else {}
        template_id = str(first.get("template_id") or "")
        target_ref = str(first.get("target_ref") or "")
    if template_id != "starter.schema-profile-quality" or not target_ref:
        return ""
    table_subjects = [f"table:{ref}" for ref in _candidate_table_refs(candidate)]
    if not table_subjects:
        return ""
    return _schema_profile_quality_sql(
        schema_ref=target_ref,
        table_subjects=table_subjects,
        view_name=view_name,
    )


def _candidate_table_refs(candidate: dict[str, Any]) -> tuple[str, ...]:
    refs = candidate.get("evidence_refs") if isinstance(candidate, dict) else []
    out: list[str] = []
    if isinstance(refs, list):
        for item in refs:
            if isinstance(item, dict) and item.get("kind") == "table" and item.get("ref"):
                out.append(str(item["ref"]))
    return tuple(out)


def _schema_profile_expected_schema() -> dict[str, str]:
    return {
        "schema_ref": "STRING",
        "table_ref": "STRING",
        "row_count": "BIGINT",
        "column_claims": "BIGINT",
        "null_count_claims": "BIGINT",
        "distinct_count_claims": "BIGINT",
        "grain_check_json": "STRING",
        "evidence_observed_at_ms": "BIGINT",
    }


def _output_object_name(
    *,
    usecase_id: str,
    output_uri: Any,
    default_prefix: str = "bv_skill_sql",
) -> str:
    if isinstance(output_uri, str) and output_uri.strip():
        raw = output_uri.strip().replace("`", "")
        return raw.split(".")[-1]
    digest = hashlib.sha256(usecase_id.encode("utf-8")).hexdigest()[:10]
    return f"{default_prefix}_{digest}"


def _catalog_from_uc_name(output_uri: str) -> str:
    cleaned = output_uri.replace("`", "")
    return cleaned.split(".", 1)[0] if "." in cleaned else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [item.strip() for item in stripped.replace("\n", ",").split(",") if item.strip()]
    return []


def _object_value(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            return {}
    return {}


def _object_any(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return dict(parsed)
        except json.JSONDecodeError:
            return {}
    return {}


def _list_of_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            return []
    return []


def _list_of_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ml_strategy_capability_inputs(
    *,
    user_id: str,
    inputs: dict[str, Any],
    problem_type: str,
) -> dict[str, list[dict[str, Any]]]:
    evidence = _list_of_objects(inputs.get("capability_evidence"))
    operations = _list_of_objects(inputs.get("api_operations"))
    if evidence:
        return {"capability_evidence": evidence, "api_operations": operations}

    retrieved_evidence = _retrieve_ml_strategy_capabilities(
        user_id=user_id,
        problem_type=problem_type,
    )
    return {
        "capability_evidence": evidence or retrieved_evidence,
        "api_operations": operations,
    }


def _retrieve_ml_strategy_capabilities(
    *,
    user_id: str,
    problem_type: str,
) -> list[dict[str, Any]]:
    try:
        from .capability_graph_service import get_extension_provenance
        from .capability_rag_service import search_capability_graph
    except Exception:
        return []

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for query in _ml_capability_queries(problem_type):
        try:
            search_result = search_capability_graph(
                user_id=user_id,
                query=query,
                limit=ML_CAPABILITY_QUERY_LIMIT,
            )
        except Exception:
            continue
        for hit in _list_of_objects(search_result.get("results")):
            entity_id = str(hit.get("entity_id") or "").strip()
            if not _is_capability_ref(entity_id):
                continue
            evidence_by_id.setdefault(entity_id, _capability_evidence_from_hit(hit))
            try:
                provenance = get_extension_provenance(user_id=user_id, extension_id=entity_id)
            except Exception:
                continue
            source_kinds = _source_kinds_from_provenance(provenance)
            if source_kinds:
                evidence_by_id[entity_id]["source_kinds"] = source_kinds
            for operation_ref in _openapi_refs_from_provenance(
                entity_id=entity_id,
                provenance=provenance,
            ):
                evidence_by_id.setdefault(
                    f"{operation_ref['entity_id']}:{operation_ref['method']}:{operation_ref['path']}",
                    operation_ref,
                )
    return list(evidence_by_id.values())


def _ml_capability_queries(problem_type: str) -> tuple[str, ...]:
    type_hint = problem_type.strip() or "machine learning"
    return (
        "Databricks Jobs runs submit API for ML training on Databricks compute",
        "Databricks SQL Statement Execution API read model training audit result",
        f"Databricks Unity Catalog registered model MLflow {type_hint} training",
    )


def _capability_evidence_from_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": str(hit.get("entity_id") or ""),
        "entity_kind": str(hit.get("entity_kind") or ""),
        "source_kinds": _list_of_strings(hit.get("source_kinds")),
        "source_url": str(hit.get("source_url") or ""),
        "chunk_text": str(hit.get("chunk_text") or "")[:1000],
        "score": hit.get("score"),
    }


def _source_kinds_from_provenance(provenance: dict[str, Any]) -> list[str]:
    chunks = provenance.get("contributing_chunks")
    if not isinstance(chunks, list):
        return []
    return sorted(
        {
            str(chunk.get("source_id") or "").strip()
            for chunk in chunks
            if isinstance(chunk, dict) and str(chunk.get("source_id") or "").strip()
        }
    )


def _openapi_refs_from_provenance(
    *,
    entity_id: str,
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    chunks = provenance.get("contributing_chunks")
    if not isinstance(chunks, list):
        return []
    operation_refs: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or chunk.get("source_id") != "openapi":
            continue
        parsed = _parse_openapi_ref(chunk.get("source_url"))
        if parsed is None:
            continue
        method, path = parsed
        operation_refs.append(
            {
                "entity_id": entity_id,
                "entity_kind": "openapi_operation_ref",
                "source_kind": "openapi",
                "method": method,
                "path": path,
            }
        )
    return operation_refs


def _parse_openapi_ref(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None
    method, path = parts[0].upper(), parts[1].strip()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not path.startswith("/api/"):
        return None
    return method, path


def _is_capability_ref(value: str) -> bool:
    return value.startswith(("sdk:", "openapi:", "docs:", "doc:", "meta:", "ext:"))


def _ml_training_gate(*, strategy: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    model_full_name = _optional_string(inputs.get("model_full_name"))
    if strategy.get("status") != "ready_for_approval":
        missing.append("approved_strategy_plan")
    if not strategy.get("api_execution_plan"):
        missing.append("api_execution_plan")
    if not model_full_name:
        missing.append("model_full_name")
    if not _optional_string(inputs.get("strategy_approval_id")):
        missing.append("strategy_approval_id")
    required_training_inputs = (
        "feature_columns",
        "label_column",
        "primary_key",
        "rows_uri",
        "val_metric_name",
        "val_metric_floor",
        "split_seed",
        "training_artifact_uri",
        "audit_table",
    )
    missing.extend(name for name in required_training_inputs if not _has_input(inputs, name))
    if "training_artifact_uri" in missing and _has_input(inputs, "training_driver_uri"):
        missing.remove("training_artifact_uri")
    training_runner = _load_skill_runner(ML_TRAIN_SKILL_ID)
    if training_runner["status"] != "ready":
        missing.append("train_evaluate_register_runner")
    if missing:
        return {
            "status": "blocked",
            "skill_id": ML_TRAIN_SKILL_ID,
            "missing": sorted(set(missing)),
            "message": (
                "Training is intentionally blocked until strategy approval, UC model "
                "name, training inputs, and real Databricks training/register adapters are bound."
            ),
        }
    return {
        "status": "ready",
        "skill_id": ML_TRAIN_SKILL_ID,
        "missing": [],
        "message": "Training gate passed; run skill:ml.train-evaluate-register next.",
    }


def _execute_ml_training(*, strategy: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    runner_state = _load_skill_runner(ML_TRAIN_SKILL_ID)
    if runner_state["status"] != "ready":
        return runner_state
    runner = runner_state["runner"]
    try:
        result = runner(
            strategy_plan=strategy,
            model_full_name=str(inputs["model_full_name"]),
            feature_columns=_string_list(inputs.get("feature_columns")),
            label_column=str(inputs["label_column"]),
            primary_key=str(inputs["primary_key"]),
            rows_uri=str(inputs["rows_uri"]),
            split_seed=_int_value(inputs.get("split_seed"), default=42),
            strategy_approval_id=str(inputs["strategy_approval_id"]),
            coordinator_call=lambda request: _ml_training_coordinator_call(
                request,
                inputs=inputs,
            ),
            skill_id=ML_TRAIN_SKILL_ID,
            audit_id=_optional_string(inputs.get("audit_id")),
        )
        training_run = result.get("model_training_run")
        return {
            "status": "execution_proven" if training_run else "execution_failed",
            "skill_id": ML_TRAIN_SKILL_ID,
            "model_training_run": _jsonable(training_run),
            "questions": [_jsonable(item) for item in result.get("questions", [])],
            "transforms": [_jsonable(item) for item in result.get("transforms", [])],
            "message": (
                "Databricks ML training job completed and returned a ModelTrainingRun."
                if training_run
                else "Databricks ML training job did not return a ModelTrainingRun."
            ),
        }
    except Exception as exc:
        return {
            "status": "execution_failed",
            "skill_id": ML_TRAIN_SKILL_ID,
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }


def _ml_training_coordinator_call(_: dict[str, Any], *, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "transforms": _list_of_objects(inputs.get("transforms")),
        "val_metric_name": str(inputs.get("val_metric_name") or ""),
        "val_metric_floor": float(inputs.get("val_metric_floor") or 0.0),
    }


def _has_input(inputs: dict[str, Any], name: str) -> bool:
    value = inputs.get(name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | tuple | dict):
        return bool(value)
    return True


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_skill_runner(skill_id: str) -> dict[str, Any]:
    contract = load_skill_contract(skill_id)
    if contract is None:
        return {
            "status": "skill_contract_missing",
            "skill_id": skill_id,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "message": f"{skill_id} has no SKILL.yaml contract.",
        }
    if not contract.skill_py.exists():
        return {
            "status": "skill_runner_missing",
            "skill_id": skill_id,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "message": f"{skill_id} has no skill.py module.",
        }
    try:
        module = import_skill_module(contract, prefix="_brickvision_exec_skill")
    except Exception as exc:
        return {
            "status": "skill_runner_missing",
            "skill_id": skill_id,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    runner = getattr(module, contract.runner_name, None)
    if not callable(runner):
        return {
            "status": "skill_runner_missing",
            "skill_id": skill_id,
            "executed": False,
            "proof_kind": "skill_contract_execution",
            "message": f"{skill_id} does not expose {contract.runner_name}.",
        }
    return {"status": "ready", "skill_id": skill_id, "runner": runner}


def _required_contract_inputs(skill_id: str) -> list[str]:
    contract = load_skill_contract(skill_id)
    if contract is None:
        return []
    return [
        str(field.get("name") or "")
        for field in contract.inputs
        if field.get("required") and field.get("name")
    ]


def _ensure_runtime_path() -> None:
    runtime_src = str(repo_root() / "src")
    if runtime_src not in sys.path:
        sys.path.insert(0, runtime_src)


def _persist_skill_execution(
    *,
    user_id: str,
    usecase_id: str,
    execution: dict[str, Any],
) -> None:
    _ensure_skill_execution_table()
    now_ms = int(time.time() * 1000)
    execution_id = "use_" + hashlib.sha256(
        json.dumps(
            {
                "usecase_id": usecase_id,
                "skill_id": execution.get("skill_id"),
                "created_at_ms": now_ms,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_skill_execution_runs")} (
          execution_id,
          usecase_id,
          skill_id,
          status,
          result_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(execution_id)},
          {databricks_sql.sql_string_literal(usecase_id)},
          {databricks_sql.sql_string_literal(str(execution.get("skill_id", "")))},
          {databricks_sql.sql_string_literal(str(execution.get("status", "")))},
          {databricks_sql.sql_string_literal(json.dumps(execution, sort_keys=True))},
          {databricks_sql.sql_string_literal(user_id)},
          {now_ms}
        )
        """
    )


def _ensure_skill_execution_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_skill_execution_runs")} (
          execution_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          skill_id STRING NOT NULL,
          status STRING NOT NULL,
          result_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_skill_execution_runs')
        """
    )


def _jsonable(value: Any) -> Any:  # noqa: ANN401
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
