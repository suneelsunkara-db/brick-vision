"""Stable usecase service façade for routers and legacy imports."""

from __future__ import annotations

from typing import Any

from .skill_builder_service import list_skill_builder_contracts
from .migration_runs import (
    get_migration_run,
    list_migration_runs,
    start_migration_run,
)
from .usecase_candidates import list_usecase_candidates
from .usecase_planner import plan_and_build_workspace_suggestion
from .usecase_records import (
    create_usecase_from_candidate,
    evaluate_usecase_go_no_go,
    generate_usecase_artifact_plan,
    get_usecase_skill_inputs,
    get_usecase_record,
    resolve_usecase_skills,
    save_usecase_skill_inputs,
    save_usecase_inputs,
    save_usecase_strategy,
    validate_usecase_artifact_plan,
)
from .usecase_tool_proofs import list_usecase_tool_proofs, run_usecase_tool_proof
from .usecase_executions import (
    get_usecase_execution,
    list_usecase_executions,
    start_usecase_execution,
)
from .usecase_suggestions import (
    PROFILE_PREDICATES,
    REQUIRED_PROFILE_PREDICATES,
    REQUIRED_SUGGESTION_ANCHORS,
    list_workspace_build_suggestions,
)
from .workspace_visibility import INTERNAL_UC_TABLE_PREFIXES

__all__ = [
    "INTERNAL_UC_TABLE_PREFIXES",
    "PROFILE_PREDICATES",
    "REQUIRED_PROFILE_PREDICATES",
    "REQUIRED_SUGGESTION_ANCHORS",
    "create_usecase_from_candidate",
    "evaluate_usecase_go_no_go",
    "generate_usecase_artifact_plan",
    "get_migration_run",
    "get_usecase_record",
    "get_usecase_execution",
    "get_usecase_skill_inputs",
    "list_skill_builder_contracts",
    "list_migration_runs",
    "list_usecase_executions",
    "list_usecase_candidates",
    "list_usecase_tool_proofs",
    "list_workspace_build_suggestions",
    "plan_and_build_workspace_suggestion",
    "resolve_usecase_skills",
    "save_usecase_inputs",
    "save_usecase_skill_inputs",
    "save_usecase_strategy",
    "start_usecase_execution",
    "start_migration_run",
    "run_usecase_tool_proof",
    "validate_usecase_artifact_plan",
]
