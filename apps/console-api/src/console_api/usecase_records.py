"""Persistent usecase records created from business candidates."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import databricks_sql
from .evaluation_events import emit_evaluation_event
from .skill_contracts import load_skill_contract, skill_ids_for_family
from .usecase_candidates import list_usecase_candidates


def create_usecase_from_candidate(
    *,
    user_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    """Persist a selected candidate as a usecase workspace record."""

    payload = list_usecase_candidates(user_id=user_id, limit=50)
    candidate = next(
        (
            item
            for item in payload.get("candidates", [])
            if item.get("candidate_id") == candidate_id
        ),
        None,
    )
    if candidate is None:
        return {
            "status": "not_found",
            "candidate_id": candidate_id,
            "message": "Candidate is not available under the current evidence gate.",
        }

    now_ms = int(time.time() * 1000)
    usecase_id = _usecase_id(candidate_id=candidate_id, created_at_ms=now_ms)
    record = {
        "usecase_id": usecase_id,
        "candidate_id": candidate_id,
        "source_suggestion_id": candidate.get("source_suggestion_id"),
        "status": "draft",
        "title": candidate.get("title"),
        "outcome": candidate.get("outcome"),
        "persona": candidate.get("persona"),
        "value_hypothesis": candidate.get("value_hypothesis"),
        "readiness": candidate.get("readiness"),
        "active_snapshot_id": payload.get("active_snapshot_id"),
        "candidate": candidate,
        "created_by": user_id,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "next_action": (
            "Fill outcome acceptance criteria, resolve missing inputs, then "
            "attach technical starter artifacts to the usecase plan."
        ),
    }
    _persist_usecase_record(record)
    return record


def get_usecase_record(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Load one persisted usecase workspace record."""

    _ = user_id
    _ensure_usecase_table()
    rows = databricks_sql.query_sql_statement_rows(
        f"""
        SELECT
          usecase_id,
          candidate_id,
          source_suggestion_id,
          status,
          title,
          outcome,
          persona,
          value_hypothesis,
          readiness,
          active_snapshot_id,
          candidate_json,
          created_by,
          created_at_ms,
          updated_at_ms
        FROM {databricks_sql.qualified_uc_name("workspace_usecases")}
        WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
        ORDER BY updated_at_ms DESC
        LIMIT 1
        """
    )
    if not rows:
        return {
            "status": "not_found",
            "usecase_id": usecase_id,
            "message": "Usecase record was not found.",
        }
    record = _row_to_record(rows[0])
    record["inputs"] = _load_latest_usecase_inputs(usecase_id=usecase_id)
    record["strategy"] = _load_latest_usecase_strategy(usecase_id=usecase_id)
    record["artifact_plan"] = _load_latest_artifact_plan(usecase_id=usecase_id)
    record["artifact_validation"] = _load_latest_artifact_validation(
        usecase_id=usecase_id
    )
    record["evaluation"] = _load_latest_evaluation(usecase_id=usecase_id)
    return record


def save_usecase_inputs(
    *,
    user_id: str,
    usecase_id: str,
    acceptance_criteria: list[str],
    missing_input_values: dict[str, Any],
) -> dict[str, Any]:
    """Append user-supplied outcome criteria and missing-input values."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    _ensure_usecase_inputs_table()
    now_ms = int(time.time() * 1000)
    input_id = _input_id(usecase_id=usecase_id, created_at_ms=now_ms)
    acceptance_json = json.dumps(
        [item.strip() for item in acceptance_criteria if item.strip()],
        sort_keys=True,
    )
    values_json = json.dumps(missing_input_values, sort_keys=True)
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_inputs")} (
          input_id,
          usecase_id,
          acceptance_criteria_json,
          missing_input_values_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(input_id)},
          {databricks_sql.sql_string_literal(usecase_id)},
          {databricks_sql.sql_string_literal(acceptance_json)},
          {databricks_sql.sql_string_literal(values_json)},
          {databricks_sql.sql_string_literal(user_id)},
          {now_ms}
        )
        """
    )
    updated = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    updated["status"] = "draft_inputs_saved"
    return updated


def save_usecase_strategy(
    *,
    user_id: str,
    usecase_id: str,
    strategy_kind: str,
    rationale: str,
) -> dict[str, Any]:
    """Append a selected build strategy for a draft usecase."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    normalized = strategy_kind.strip().lower()
    if normalized not in _ALLOWED_STRATEGY_KINDS:
        return {
            "status": "invalid_strategy",
            "usecase_id": usecase_id,
            "message": "Strategy kind is not supported.",
            "allowed_strategy_kinds": sorted(_ALLOWED_STRATEGY_KINDS),
        }

    _ensure_usecase_strategy_table()
    now_ms = int(time.time() * 1000)
    strategy_id = _strategy_id(usecase_id=usecase_id, created_at_ms=now_ms)
    skill_families_json = json.dumps(
        _strategy_skill_families(normalized),
        sort_keys=True,
    )
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_strategy")} (
          strategy_id,
          usecase_id,
          strategy_kind,
          rationale,
          required_skill_families_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(strategy_id)},
          {databricks_sql.sql_string_literal(usecase_id)},
          {databricks_sql.sql_string_literal(normalized)},
          {databricks_sql.sql_string_literal(rationale.strip())},
          {databricks_sql.sql_string_literal(skill_families_json)},
          {databricks_sql.sql_string_literal(user_id)},
          {now_ms}
        )
        """
    )
    updated = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    updated["status"] = "draft_strategy_selected"
    return updated


def resolve_usecase_skills(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Resolve selected strategy requirements into skill-family statuses."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    strategy = record.get("strategy")
    if not isinstance(strategy, dict):
        return {
            "status": "strategy_required",
            "usecase_id": usecase_id,
            "message": "Select a strategy before resolving skills.",
            "skills": [],
            "next_action": "Choose SQL, PySpark, ML, AI, migration, or composite strategy.",
        }

    required = [
        str(item)
        for item in strategy.get("required_skill_families", [])
        if str(item).strip()
    ]
    strategy_kind = str(strategy.get("strategy_kind") or "")
    skills = [
        _skill_status(family, strategy_kind=strategy_kind)
        for family in required
    ]
    missing = [
        skill
        for skill in skills
            if skill["status"] not in {"available", "covered"}
    ]
    return {
        "status": "resolved_with_gaps" if missing else "ready",
        "usecase_id": usecase_id,
        "strategy_kind": strategy_kind,
        "skills": skills,
        "missing_count": len(missing),
        "next_action": (
            "Open Skill Builder for missing skills before generating artifacts."
            if missing
            else "Proceed to artifact planning."
        ),
    }


def get_usecase_skill_inputs(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Return required input bindings for resolvable existing skills."""

    resolution = resolve_usecase_skills(user_id=user_id, usecase_id=usecase_id)
    if resolution.get("status") in {"not_found", "strategy_required"}:
        return {
            **resolution,
            "requirements": [],
            "bindings": {},
        }

    bindings = _load_latest_skill_input_bindings(usecase_id=usecase_id)
    requirements = [
        _skill_input_requirement(skill)
        for skill in resolution.get("skills", [])
        if skill.get("status") in {"available", "covered", "needs_inputs"}
    ]
    for requirement in requirements:
        family = str(requirement["family"])
        requirement["binding"] = bindings.get(family)
        requirement["binding_status"] = (
            "bound" if bindings.get(family) else "unbound"
        )

    unbound = [
        item for item in requirements
        if item.get("required") and item.get("binding_status") != "bound"
    ]
    return {
        "status": "ready_to_plan_artifacts" if not unbound else "needs_input_bindings",
        "usecase_id": usecase_id,
        "requirements": requirements,
        "bindings": bindings,
        "missing_binding_count": len(unbound),
        "next_action": (
            "Generate artifact plan from bound skill inputs."
            if not unbound
            else "Bind required skill inputs before artifact planning."
        ),
    }


def save_usecase_skill_inputs(
    *,
    user_id: str,
    usecase_id: str,
    family: str,
    skill_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Append one skill-family input binding for a usecase."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    normalized_family = family.strip()
    if not normalized_family:
        return {
            "status": "invalid_binding",
            "usecase_id": usecase_id,
            "message": "Skill family is required.",
        }

    _ensure_skill_inputs_table()
    now_ms = int(time.time() * 1000)
    binding_id = _skill_binding_id(
        usecase_id=usecase_id,
        family=normalized_family,
        created_at_ms=now_ms,
    )
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_skill_inputs")} (
          binding_id,
          usecase_id,
          family,
          skill_id,
          inputs_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(binding_id)},
          {databricks_sql.sql_string_literal(usecase_id)},
          {databricks_sql.sql_string_literal(normalized_family)},
          {databricks_sql.sql_string_literal(skill_id.strip())},
          {databricks_sql.sql_string_literal(json.dumps(inputs, sort_keys=True))},
          {databricks_sql.sql_string_literal(user_id)},
          {now_ms}
        )
        """
    )
    return get_usecase_skill_inputs(user_id=user_id, usecase_id=usecase_id)


def generate_usecase_artifact_plan(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Persist a draft artifact plan from selected strategy and bound inputs."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    input_state = get_usecase_skill_inputs(user_id=user_id, usecase_id=usecase_id)
    if input_state.get("status") != "ready_to_plan_artifacts":
        return {
            "status": "blocked_missing_skill_inputs",
            "usecase_id": usecase_id,
            "message": "Bind required skill inputs before artifact planning.",
            "skill_input_state": input_state,
        }

    strategy = record.get("strategy") if isinstance(record.get("strategy"), dict) else {}
    now_ms = int(time.time() * 1000)
    plan_id = _artifact_plan_id(usecase_id=usecase_id, created_at_ms=now_ms)
    plan = {
        "artifact_plan_id": plan_id,
        "usecase_id": usecase_id,
        "status": "draft",
        "strategy_kind": strategy.get("strategy_kind"),
        "steps": _artifact_steps_from_requirements(input_state["requirements"]),
        "created_by": user_id,
        "created_at_ms": now_ms,
        "next_action": "Review artifact plan, then run validation before execution.",
    }
    _persist_artifact_plan(plan)
    return plan


def validate_usecase_artifact_plan(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Validate the latest draft artifact plan before execution."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    artifact_plan = record.get("artifact_plan")
    if not isinstance(artifact_plan, dict):
        payload = {
            "status": "artifact_plan_required",
            "usecase_id": usecase_id,
            "message": "Generate an artifact plan before validation.",
            "findings": [
                {
                    "severity": "blocking",
                    "code": "ARTIFACT_PLAN_MISSING",
                    "message": "No artifact plan exists for this usecase.",
                }
            ],
        }
        _emit_usecase_event(
            user_id=user_id,
            usecase_id=usecase_id,
            event_kind="usecase_stage",
            status="blocked",
            stage="artifact_validation",
            payload=payload,
            reason_codes=["ARTIFACT_PLAN_MISSING"],
        )
        return payload

    strategy = record.get("strategy") if isinstance(record.get("strategy"), dict) else {}
    strategy_kind = str(strategy.get("strategy_kind") or "")
    findings = _validate_artifact_steps(
        steps=list(artifact_plan.get("steps") or []),
        strategy_kind=strategy_kind,
    )
    status = (
        "passed"
        if not any(item["severity"] == "blocking" for item in findings)
        else "failed"
    )
    now_ms = int(time.time() * 1000)
    validation = {
        "validation_id": _artifact_validation_id(
            usecase_id=usecase_id,
            created_at_ms=now_ms,
        ),
        "usecase_id": usecase_id,
        "artifact_plan_id": artifact_plan.get("artifact_plan_id"),
        "status": status,
        "findings": findings,
        "created_by": user_id,
        "created_at_ms": now_ms,
        "next_action": (
            "Artifact plan is valid. Execution can be introduced behind approval gates."
            if status == "passed"
            else "Fix blocking findings before execution."
        ),
    }
    _persist_artifact_validation(validation)
    _emit_usecase_event(
        user_id=user_id,
        usecase_id=usecase_id,
        event_kind="usecase_stage",
        status=status,
        stage="artifact_validation",
        payload=validation,
        reason_codes=[
            str(item.get("code"))
            for item in findings
            if item.get("severity") == "blocking" and item.get("code")
        ],
    )
    return validation


def evaluate_usecase_go_no_go(*, user_id: str, usecase_id: str) -> dict[str, Any]:
    """Persist an execution go/no-go decision for a validated usecase plan."""

    record = get_usecase_record(user_id=user_id, usecase_id=usecase_id)
    if record.get("status") == "not_found":
        return record

    validation = (
        record.get("artifact_validation")
        if isinstance(record.get("artifact_validation"), dict)
        else None
    )
    skill_resolution = resolve_usecase_skills(user_id=user_id, usecase_id=usecase_id)
    blockers: list[dict[str, str]] = []

    if not validation:
        blockers.append(
            {
                "code": "VALIDATION_REQUIRED",
                "message": "Validate the artifact plan before evaluating go/no-go.",
            }
        )
    elif validation.get("status") != "passed":
        blockers.append(
            {
                "code": "VALIDATION_NOT_PASSED",
                "message": "Latest artifact validation has not passed.",
            }
        )

    for skill in skill_resolution.get("skills", []):
        status = str(skill.get("status") or "")
        if status in {"needs_skill_builder", "needs_partner_skill"}:
            blockers.append(
                {
                    "code": "UNRESOLVED_SKILL_GAP",
                    "message": (
                        f"{skill.get('family')} requires Skill Builder or partner "
                        "skill resolution before execution."
                    ),
                }
            )

    decision = "ready_for_execution" if not blockers else "blocked"
    now_ms = int(time.time() * 1000)
    evaluation = {
        "evaluation_id": _evaluation_id(usecase_id=usecase_id, created_at_ms=now_ms),
        "usecase_id": usecase_id,
        "status": decision,
        "decision": decision,
        "blockers": blockers,
        "created_by": user_id,
        "created_at_ms": now_ms,
        "next_action": (
            "Proceed to gated execution for validated SQL/PySpark/ML paths."
            if decision == "ready_for_execution"
            else "Resolve blockers before execution can be enabled."
        ),
    }
    _persist_evaluation(evaluation)
    _emit_usecase_event(
        user_id=user_id,
        usecase_id=usecase_id,
        event_kind="usecase_stage",
        status="passed" if decision == "ready_for_execution" else "blocked",
        stage="go_no_go",
        payload=evaluation,
        reason_codes=[str(item.get("code")) for item in blockers if item.get("code")],
    )
    return evaluation


def _persist_usecase_record(record: dict[str, Any]) -> None:
    _ensure_usecase_table()
    candidate_json = json.dumps(record.get("candidate", {}), sort_keys=True)
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecases")} (
          usecase_id,
          candidate_id,
          source_suggestion_id,
          status,
          title,
          outcome,
          persona,
          value_hypothesis,
          readiness,
          active_snapshot_id,
          candidate_json,
          created_by,
          created_at_ms,
          updated_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(record.get("usecase_id", "")))},
          {databricks_sql.sql_string_literal(str(record.get("candidate_id", "")))},
          {databricks_sql.sql_string_literal(str(record.get("source_suggestion_id") or ""))},
          {databricks_sql.sql_string_literal(str(record.get("status", "")))},
          {databricks_sql.sql_string_literal(str(record.get("title", "")))},
          {databricks_sql.sql_string_literal(str(record.get("outcome", "")))},
          {databricks_sql.sql_string_literal(str(record.get("persona", "")))},
          {databricks_sql.sql_string_literal(str(record.get("value_hypothesis", "")))},
          {databricks_sql.sql_string_literal(str(record.get("readiness", "")))},
          {databricks_sql.sql_string_literal(str(record.get("active_snapshot_id") or ""))},
          {databricks_sql.sql_string_literal(candidate_json)},
          {databricks_sql.sql_string_literal(str(record.get("created_by", "")))},
          {int(record.get("created_at_ms") or 0)},
          {int(record.get("updated_at_ms") or 0)}
        )
        """
    )


def _ensure_usecase_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecases")} (
          usecase_id STRING NOT NULL,
          candidate_id STRING NOT NULL,
          source_suggestion_id STRING,
          status STRING NOT NULL,
          title STRING,
          outcome STRING,
          persona STRING,
          value_hypothesis STRING,
          readiness STRING,
          active_snapshot_id STRING,
          candidate_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL,
          updated_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecases')
        """
    )


def _ensure_usecase_inputs_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_inputs")} (
          input_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          acceptance_criteria_json STRING,
          missing_input_values_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_inputs')
        """
    )


def _ensure_usecase_strategy_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_strategy")} (
          strategy_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          strategy_kind STRING NOT NULL,
          rationale STRING,
          required_skill_families_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_strategy')
        """
    )


def _ensure_skill_inputs_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_skill_inputs")} (
          binding_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          family STRING NOT NULL,
          skill_id STRING,
          inputs_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_skill_inputs')
        """
    )


def _ensure_artifact_plan_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_artifact_plans")} (
          artifact_plan_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          status STRING NOT NULL,
          strategy_kind STRING,
          steps_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_artifact_plans')
        """
    )


def _persist_artifact_plan(plan: dict[str, Any]) -> None:
    _ensure_artifact_plan_table()
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_artifact_plans")} (
          artifact_plan_id,
          usecase_id,
          status,
          strategy_kind,
          steps_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(plan.get("artifact_plan_id", "")))},
          {databricks_sql.sql_string_literal(str(plan.get("usecase_id", "")))},
          {databricks_sql.sql_string_literal(str(plan.get("status", "")))},
          {databricks_sql.sql_string_literal(str(plan.get("strategy_kind") or ""))},
          {databricks_sql.sql_string_literal(json.dumps(plan.get("steps", []), sort_keys=True))},
          {databricks_sql.sql_string_literal(str(plan.get("created_by", "")))},
          {int(plan.get("created_at_ms") or 0)}
        )
        """
    )


def _ensure_artifact_validation_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_artifact_validations")} (
          validation_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          artifact_plan_id STRING,
          status STRING NOT NULL,
          findings_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_artifact_validations')
        """
    )


def _persist_artifact_validation(validation: dict[str, Any]) -> None:
    _ensure_artifact_validation_table()
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_artifact_validations")} (
          validation_id,
          usecase_id,
          artifact_plan_id,
          status,
          findings_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(validation.get("validation_id", "")))},
          {databricks_sql.sql_string_literal(str(validation.get("usecase_id", "")))},
          {databricks_sql.sql_string_literal(str(validation.get("artifact_plan_id") or ""))},
          {databricks_sql.sql_string_literal(str(validation.get("status", "")))},
          {databricks_sql.sql_string_literal(json.dumps(validation.get("findings", []), sort_keys=True))},
          {databricks_sql.sql_string_literal(str(validation.get("created_by", "")))},
          {int(validation.get("created_at_ms") or 0)}
        )
        """
    )


def _ensure_evaluation_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_usecase_evaluations")} (
          evaluation_id STRING NOT NULL,
          usecase_id STRING NOT NULL,
          status STRING NOT NULL,
          decision STRING NOT NULL,
          blockers_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_usecase_evaluations')
        """
    )


def _persist_evaluation(evaluation: dict[str, Any]) -> None:
    _ensure_evaluation_table()
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_usecase_evaluations")} (
          evaluation_id,
          usecase_id,
          status,
          decision,
          blockers_json,
          created_by,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(evaluation.get("evaluation_id", "")))},
          {databricks_sql.sql_string_literal(str(evaluation.get("usecase_id", "")))},
          {databricks_sql.sql_string_literal(str(evaluation.get("status", "")))},
          {databricks_sql.sql_string_literal(str(evaluation.get("decision", "")))},
          {databricks_sql.sql_string_literal(json.dumps(evaluation.get("blockers", []), sort_keys=True))},
          {databricks_sql.sql_string_literal(str(evaluation.get("created_by", "")))},
          {int(evaluation.get("created_at_ms") or 0)}
        )
        """
    )


def _load_latest_usecase_inputs(*, usecase_id: str) -> dict[str, Any]:
    try:
        _ensure_usecase_inputs_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              input_id,
              acceptance_criteria_json,
              missing_input_values_json,
              created_by,
              created_at_ms
            FROM {databricks_sql.qualified_uc_name("workspace_usecase_inputs")}
            WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            ORDER BY created_at_ms DESC
            LIMIT 1
            """
        )
    except Exception:
        return {
            "acceptance_criteria": [],
            "missing_input_values": {},
        }
    if not rows:
        return {
            "acceptance_criteria": [],
            "missing_input_values": {},
        }
    row = rows[0]
    criteria = _decode_json(row[1])
    values = _decode_json(row[2])
    return {
        "input_id": str(row[0]),
        "acceptance_criteria": criteria if isinstance(criteria, list) else [],
        "missing_input_values": values if isinstance(values, dict) else {},
        "created_by": str(row[3]) if row[3] is not None else "",
        "created_at_ms": int(row[4]) if row[4] is not None else None,
    }


def _load_latest_usecase_strategy(*, usecase_id: str) -> dict[str, Any] | None:
    try:
        _ensure_usecase_strategy_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              strategy_id,
              strategy_kind,
              rationale,
              required_skill_families_json,
              created_by,
              created_at_ms
            FROM {databricks_sql.qualified_uc_name("workspace_usecase_strategy")}
            WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            ORDER BY created_at_ms DESC
            LIMIT 1
            """
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    skill_families = _decode_json(row[3])
    return {
        "strategy_id": str(row[0]),
        "strategy_kind": str(row[1]),
        "rationale": str(row[2]) if row[2] is not None else "",
        "required_skill_families": (
            skill_families if isinstance(skill_families, list) else []
        ),
        "created_by": str(row[4]) if row[4] is not None else "",
        "created_at_ms": int(row[5]) if row[5] is not None else None,
    }


def _load_latest_skill_input_bindings(*, usecase_id: str) -> dict[str, Any]:
    try:
        _ensure_skill_inputs_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              family,
              skill_id,
              inputs_json,
              created_by,
              created_at_ms
            FROM (
              SELECT
                *,
                ROW_NUMBER() OVER (
                  PARTITION BY family
                  ORDER BY created_at_ms DESC
                ) AS rn
              FROM {databricks_sql.qualified_uc_name("workspace_usecase_skill_inputs")}
              WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            )
            WHERE rn = 1
            """
        )
    except Exception:
        return {}

    bindings: dict[str, Any] = {}
    for row in rows:
        family = str(row[0])
        inputs = _decode_json(row[2])
        bindings[family] = {
            "family": family,
            "skill_id": str(row[1]) if row[1] is not None else "",
            "inputs": inputs if isinstance(inputs, dict) else {},
            "created_by": str(row[3]) if row[3] is not None else "",
            "created_at_ms": int(row[4]) if row[4] is not None else None,
        }
    return bindings


def _load_latest_artifact_plan(*, usecase_id: str) -> dict[str, Any] | None:
    try:
        _ensure_artifact_plan_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              artifact_plan_id,
              status,
              strategy_kind,
              steps_json,
              created_by,
              created_at_ms
            FROM {databricks_sql.qualified_uc_name("workspace_usecase_artifact_plans")}
            WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            ORDER BY created_at_ms DESC
            LIMIT 1
            """
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    steps = _decode_json(row[3])
    return {
        "artifact_plan_id": str(row[0]),
        "usecase_id": usecase_id,
        "status": str(row[1]),
        "strategy_kind": str(row[2]) if row[2] is not None else "",
        "steps": steps if isinstance(steps, list) else [],
        "created_by": str(row[4]) if row[4] is not None else "",
        "created_at_ms": int(row[5]) if row[5] is not None else None,
    }


def _load_latest_artifact_validation(*, usecase_id: str) -> dict[str, Any] | None:
    try:
        _ensure_artifact_validation_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              validation_id,
              artifact_plan_id,
              status,
              findings_json,
              created_by,
              created_at_ms
            FROM {databricks_sql.qualified_uc_name("workspace_usecase_artifact_validations")}
            WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            ORDER BY created_at_ms DESC
            LIMIT 1
            """
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    findings = _decode_json(row[3])
    return {
        "validation_id": str(row[0]),
        "usecase_id": usecase_id,
        "artifact_plan_id": str(row[1]) if row[1] is not None else "",
        "status": str(row[2]),
        "findings": findings if isinstance(findings, list) else [],
        "created_by": str(row[4]) if row[4] is not None else "",
        "created_at_ms": int(row[5]) if row[5] is not None else None,
    }


def _load_latest_evaluation(*, usecase_id: str) -> dict[str, Any] | None:
    try:
        _ensure_evaluation_table()
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              evaluation_id,
              status,
              decision,
              blockers_json,
              created_by,
              created_at_ms
            FROM {databricks_sql.qualified_uc_name("workspace_usecase_evaluations")}
            WHERE usecase_id = {databricks_sql.sql_string_literal(usecase_id)}
            ORDER BY created_at_ms DESC
            LIMIT 1
            """
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    blockers = _decode_json(row[3])
    return {
        "evaluation_id": str(row[0]),
        "usecase_id": usecase_id,
        "status": str(row[1]),
        "decision": str(row[2]),
        "blockers": blockers if isinstance(blockers, list) else [],
        "created_by": str(row[4]) if row[4] is not None else "",
        "created_at_ms": int(row[5]) if row[5] is not None else None,
    }


_ALLOWED_STRATEGY_KINDS = {
    "sql_only",
    "pyspark_pipeline",
    "ml_workflow",
    "ai_agent",
    "migration_assessment",
    "composite",
}


def _strategy_skill_families(strategy_kind: str) -> list[str]:
    mapping = {
        "sql_only": ["SQL", "Deploy"],
        "pyspark_pipeline": ["SQL", "PySpark", "Deploy"],
        "ml_workflow": ["SQL", "PySpark", "ML", "Deploy"],
        "ai_agent": ["SQL", "AI", "Deploy"],
        "migration_assessment": ["SQL", "Migration", "PySpark", "Deploy"],
        "composite": ["SQL", "PySpark", "ML", "AI", "Migration", "Deploy"],
    }
    return mapping.get(strategy_kind, [])


def _skill_status(family: str, *, strategy_kind: str) -> dict[str, str]:
    normalized = family.strip()
    if normalized == "SQL":
        skill_id = _primary_skill_id("SQL")
        return {
            "family": "SQL",
            "status": "available",
            "reason": (
                "Existing SQL skill chain is available: SQL transform followed "
                "by Databricks Statement Execution binding."
            ),
            "skill_id": skill_id,
            "next_action": "Bind SQL transform inputs, warehouse_id, and Statement Execution capability evidence.",
        }
    if normalized == "PySpark":
        skill_id = _primary_skill_id("PySpark")
        return {
            "family": "PySpark",
            "status": "needs_inputs",
            "reason": (
                "Existing PySpark skill chain is available, but this usecase must bind "
                "transform inputs, a deployed driver artifact, and Jobs capability evidence."
            ),
            "skill_id": skill_id,
            "next_action": "Bind pipeline inputs, transform_code, pyspark_driver_uri, and Jobs capability evidence.",
        }
    if normalized == "ML":
        skill_ids = skill_ids_for_family("ML")
        return {
            "family": "ML",
            "status": "needs_inputs",
            "reason": (
                "ML runs through readiness, strategy, model-family, backend selection, "
                "Jobs/API binding, and Unity Catalog model registration gates."
            ),
            "skill_id": ", ".join(skill_ids),
            "next_action": "Bind ML readiness, backend evidence, task, and training inputs.",
        }
    if normalized == "AI":
        return {
            "family": "AI",
            "status": "needs_skill_builder",
            "reason": (
                "The repo has documentation lookup support, but no validated "
                "RAG/agent builder skill contract for this usecase family yet."
            ),
            "skill_id": "",
            "next_action": "Use Skill Builder to create a RAG/agent builder skill from capability anchors.",
        }
    if normalized == "Migration":
        return {
            "family": "Migration",
            "status": "needs_partner_skill",
            "reason": (
                "Lakebridge SQL transpilation was proven as a starter, but there "
                "is no signed partner Lakebridge skill pack in the local skill catalog."
            ),
            "skill_id": "skill:partner.<partner_id>.migration.lakebridge-sql-transpile",
            "next_action": "Validate partner Lakebridge skill pack and runtime requirements.",
        }
    if normalized == "Deploy":
        if strategy_kind in {"sql_only", "pyspark_pipeline"}:
            return {
                "family": "Deploy",
                "status": "covered",
                "reason": (
                    "For this strategy, deployment is covered by the selected "
                    "Delta transform skill once output target and approvals are bound."
                ),
                "skill_id": (
                    "skill:delta.sql-transform"
                    if strategy_kind == "sql_only"
                    else "skill:delta.pyspark-transform"
                ),
                "next_action": "Bind output target and approval policy before execution.",
            }
        if strategy_kind == "ml_workflow":
            return {
                "family": "Deploy",
                "status": "needs_inputs",
                "reason": (
                    "Existing Mosaic AI serving deploy skill is available, but it "
                    "requires model_full_name, alias, endpoint_name, and HITL approval."
                ),
                "skill_id": "skill:ml.serve-deploy",
                "next_action": "Register model, assign alias, choose endpoint, then request approval.",
            }
        return {
            "family": "Deploy",
            "status": "needs_inputs",
            "reason": (
                "Deployment depends on the chosen artifact family and needs a "
                "target plus approval policy before execution."
            ),
            "skill_id": "",
            "next_action": "Choose job/app/dashboard/endpoint target and approval policy.",
        }
    return {
        "family": normalized,
        "status": "unknown",
        "reason": "Skill family is not recognized by the current resolver.",
        "skill_id": "",
        "next_action": "Review strategy and map this family in Skill Builder.",
    }


def _skill_input_requirement(skill: dict[str, Any]) -> dict[str, Any]:
    family = str(skill.get("family") or "")
    skill_id = str(skill.get("skill_id") or "")
    if "," in skill_id:
        skill_id = skill_id.split(",", 1)[0].strip()
    fields = [] if skill.get("status") == "covered" else _input_fields_for_family(family)
    return {
        "family": family,
        "skill_id": skill_id,
        "status": skill.get("status"),
        "required": bool(fields),
        "fields": fields,
    }


def _input_fields_for_family(family: str) -> list[dict[str, Any]]:
    if family == "SQL":
        fields = _contract_input_fields("skill:delta.sql-transform")
        fields.extend(
            field for field in _contract_input_fields("skill:databricks.statement-execute")
            if field["name"] in {"capability_evidence", "statement_operation"}
            and field["name"] not in {item["name"] for item in fields}
        )
        return fields
    if family == "PySpark":
        fields = _contract_input_fields("skill:delta.pyspark-transform")
        fields.append(
            {
                "name": "transform_code",
                "type": "string",
                "required": True,
                "description": "Validated PySpark transform code emitted by the skill/codegen path.",
            }
        )
        fields.extend(
            field for field in _contract_input_fields("skill:delta.pyspark-task-plan")
            if field["name"] in {"pyspark_driver_uri", "job_run_name", "timeout_seconds"}
            and field["name"] not in {item["name"] for item in fields}
        )
        fields.extend(
            [
                {
                    "name": "jobs_capability_evidence",
                    "type": "object[]",
                    "required": True,
                    "description": "Indexed Jobs API capability evidence for skill:lakeflow.jobs-run-submit.",
                },
                {
                    "name": "job_submit_operation",
                    "type": "object",
                    "required": False,
                    "description": "Optional pre-bound Jobs runs/submit operation override.",
                },
            ]
        )
        return fields
    if family == "ML":
        return _ml_readiness_input_fields()
    if family == "Deploy":
        return _contract_input_fields("skill:ml.serve-deploy")
    return []


def _primary_skill_id(family: str) -> str:
    skill_ids = skill_ids_for_family(family)
    return skill_ids[0] if skill_ids else ""


def _contract_input_fields(skill_id: str) -> list[dict[str, Any]]:
    if not skill_id:
        return []
    contract = load_skill_contract(skill_id)
    if contract is None:
        return []
    return [dict(field) for field in contract.inputs]


def _ml_readiness_input_fields() -> list[dict[str, Any]]:
    fields = _contract_input_fields("skill:ml.problem-select")
    for skill_id, names in {
        "skill:ml.feature-readiness": {
            "target_column",
            "entity_key",
            "time_column",
        },
        "skill:ml.strategy-plan": {
            "model_full_name",
            "capability_evidence",
            "api_operations",
        },
        "skill:ml.training-backend-probe": {
            "runtime_surface",
            "probe_driver_uri",
            "probe_output_table",
            "probe_id",
            "probe_result",
        },
        "skill:ml.training-backend-select": {
            "backend_capability_evidence",
            "runtime_evidence",
        },
        "skill:ml.training-artifact-plan": {
            "training_artifact_uri",
            "artifact_template_id",
        },
        "skill:ml.training-task-plan": {
            "training_artifact_uri",
            "training_driver_uri",
            "task_parameters",
            "environment_dependencies",
            "rows_uri",
            "feature_columns",
            "label_column",
            "primary_key",
            "val_metric_name",
            "val_metric_floor",
            "split_seed",
            "strategy_approval_id",
            "audit_table",
            "audit_id",
            "job_run_name",
        },
        "skill:ml.api-plan-bind": {
            "audit_readback_operation",
        },
    }.items():
        for field in _contract_input_fields(skill_id):
            if field["name"] in names and field["name"] not in {item["name"] for item in fields}:
                fields.append(dict(field))
    if "backend_capability_evidence" not in {item["name"] for item in fields}:
        fields.append(
            {
                "name": "backend_capability_evidence",
                "type": "object[]",
                "required": False,
                "description": "Optional backend-specific capability evidence; falls back to strategy capability evidence.",
            }
        )
    return fields


def _artifact_steps_from_requirements(
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for requirement in requirements:
        binding = requirement.get("binding")
        inputs = binding.get("inputs", {}) if isinstance(binding, dict) else {}
        family = str(requirement.get("family") or "")
        if family == "SQL":
            steps.append(
                {
                    "step_id": "generate-sql-transform",
                    "family": family,
                    "skill_id": requirement.get("skill_id"),
                    "artifact_kind": "sql_delta_transform",
                    "status": "planned",
                    "bound_inputs": inputs,
                }
            )
        elif family == "PySpark":
            steps.append(
                {
                    "step_id": "generate-pyspark-transform",
                    "family": family,
                    "skill_id": requirement.get("skill_id"),
                    "artifact_kind": "pyspark_delta_transform",
                    "status": "planned",
                    "bound_inputs": inputs,
                }
            )
        elif family == "ML":
            steps.append(
                {
                    "step_id": "assess-ml-readiness",
                    "family": family,
                    "skill_id": requirement.get("skill_id"),
                    "artifact_kind": "ml_readiness_assessment",
                    "status": "planned",
                    "bound_inputs": inputs,
                }
            )
        elif family == "Deploy":
            if inputs:
                steps.append(
                    {
                        "step_id": "deploy-serving-endpoint",
                        "family": family,
                        "skill_id": requirement.get("skill_id"),
                        "artifact_kind": "model_serving_endpoint",
                        "status": "planned",
                        "bound_inputs": inputs,
                    }
                )
    return steps


_ALLOWED_ARTIFACT_KINDS_BY_STRATEGY = {
    "sql_only": {"sql_delta_transform"},
    "pyspark_pipeline": {"sql_delta_transform", "pyspark_delta_transform"},
    "ml_workflow": {
        "sql_delta_transform",
        "pyspark_delta_transform",
        "ml_readiness_assessment",
        "model_serving_endpoint",
    },
    "ai_agent": set(),
    "migration_assessment": set(),
    "composite": {
        "sql_delta_transform",
        "pyspark_delta_transform",
        "ml_readiness_assessment",
        "model_serving_endpoint",
    },
}


def _validate_artifact_steps(
    *,
    steps: list[dict[str, Any]],
    strategy_kind: str,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not steps:
        findings.append(
            {
                "severity": "blocking",
                "code": "ARTIFACT_STEPS_MISSING",
                "message": "Artifact plan has no executable steps.",
            }
        )
        return findings

    allowed_kinds = _ALLOWED_ARTIFACT_KINDS_BY_STRATEGY.get(strategy_kind, set())
    if not allowed_kinds:
        findings.append(
            {
                "severity": "blocking",
                "code": "STRATEGY_REQUIRES_SKILL_BUILDER",
                "message": (
                    f"Strategy {strategy_kind or '(unset)'} requires validated "
                    "Skill Builder or partner skill resolution before artifacts."
                ),
            }
        )

    for step in steps:
        step_id = str(step.get("step_id") or "(unknown step)")
        skill_id = str(step.get("skill_id") or "")
        artifact_kind = str(step.get("artifact_kind") or "")
        bound_inputs = step.get("bound_inputs")
        if not skill_id:
            findings.append(
                {
                    "severity": "blocking",
                    "code": "STEP_SKILL_ID_MISSING",
                    "message": f"{step_id} does not have a skill_id.",
                }
            )
        if artifact_kind not in allowed_kinds:
            findings.append(
                {
                    "severity": "blocking",
                    "code": "ARTIFACT_KIND_NOT_ALLOWED_FOR_STRATEGY",
                    "message": (
                        f"{step_id} produces {artifact_kind}, which is not "
                        f"allowed for strategy {strategy_kind}."
                    ),
                }
            )
        if not isinstance(bound_inputs, dict) or not bound_inputs:
            findings.append(
                {
                    "severity": "blocking",
                    "code": "STEP_INPUTS_MISSING",
                    "message": f"{step_id} has no bound inputs.",
                }
            )
        if artifact_kind == "model_serving_endpoint":
            for required in ("endpoint_name", "model_full_name", "alias"):
                if not isinstance(bound_inputs, dict) or not bound_inputs.get(required):
                    findings.append(
                        {
                            "severity": "blocking",
                            "code": "DEPLOY_INPUT_MISSING",
                            "message": f"{step_id} is missing deploy input {required}.",
                        }
                    )

    if not findings:
        findings.append(
            {
                "severity": "info",
                "code": "ARTIFACT_PLAN_VALID",
                "message": "Artifact plan passed static validation.",
            }
        )
    return findings


def _row_to_record(row: list[Any]) -> dict[str, Any]:
    candidate = _decode_json(row[10])
    return {
        "usecase_id": str(row[0]),
        "candidate_id": str(row[1]),
        "source_suggestion_id": str(row[2]) if row[2] is not None else "",
        "status": str(row[3]),
        "title": str(row[4]) if row[4] is not None else "",
        "outcome": str(row[5]) if row[5] is not None else "",
        "persona": str(row[6]) if row[6] is not None else "",
        "value_hypothesis": str(row[7]) if row[7] is not None else "",
        "readiness": str(row[8]) if row[8] is not None else "",
        "active_snapshot_id": str(row[9]) if row[9] is not None else "",
        "candidate": candidate if isinstance(candidate, dict) else {},
        "created_by": str(row[11]) if row[11] is not None else "",
        "created_at_ms": int(row[12]) if row[12] is not None else None,
        "updated_at_ms": int(row[13]) if row[13] is not None else None,
    }


def _usecase_id(*, candidate_id: str, created_at_ms: int) -> str:
    _ = created_at_ms
    raw = json.dumps({"candidate_id": candidate_id}, sort_keys=True)
    return "uc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _input_id(*, usecase_id: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {"usecase_id": usecase_id, "created_at_ms": created_at_ms},
        sort_keys=True,
    )
    return "uci_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _strategy_id(*, usecase_id: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {"usecase_id": usecase_id, "created_at_ms": created_at_ms},
        sort_keys=True,
    )
    return "ucs_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _skill_binding_id(*, usecase_id: str, family: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {
            "usecase_id": usecase_id,
            "family": family,
            "created_at_ms": created_at_ms,
        },
        sort_keys=True,
    )
    return "ucb_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _artifact_plan_id(*, usecase_id: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {"usecase_id": usecase_id, "created_at_ms": created_at_ms},
        sort_keys=True,
    )
    return "ucap_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _artifact_validation_id(*, usecase_id: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {"usecase_id": usecase_id, "created_at_ms": created_at_ms},
        sort_keys=True,
    )
    return "ucav_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _evaluation_id(*, usecase_id: str, created_at_ms: int) -> str:
    raw = json.dumps(
        {"usecase_id": usecase_id, "created_at_ms": created_at_ms},
        sort_keys=True,
    )
    return "ucev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _decode_json(value: Any) -> Any:  # noqa: ANN401
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _emit_usecase_event(
    *,
    user_id: str,
    usecase_id: str,
    event_kind: str,
    status: str,
    stage: str,
    payload: dict[str, Any],
    reason_codes: list[str],
) -> None:
    findings = payload.get("findings")
    blockers = payload.get("blockers")
    emit_evaluation_event(
        event_kind=event_kind,
        workflow="usecase_lifecycle",
        status=status,
        subject_id=usecase_id,
        user_id=user_id,
        metrics={
            "finding_count": len(findings) if isinstance(findings, list) else 0,
            "blocking_finding_count": (
                len([
                    item
                    for item in findings
                    if isinstance(item, dict) and item.get("severity") == "blocking"
                ])
                if isinstance(findings, list)
                else 0
            ),
            "blocker_count": len(blockers) if isinstance(blockers, list) else 0,
        },
        inputs={"stage": stage, "usecase_id": usecase_id},
        outputs=payload,
        evidence=[],
        reason_codes=reason_codes,
    )
