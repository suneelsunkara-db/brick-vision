"""Scorer registry for ``skill:delta.sql-transform``."""

from __future__ import annotations

from brickvision_runtime.eval.scorers.write_side import (
    forbidden_import_lint,
    pipeline_schema_drift,
    transform_parse_validity,
    write_target_binding_hitl,
)

SKILL_ID = "skill:delta.sql-transform"

SCORERS = {
    "TransformParseValidity": transform_parse_validity,
    "ForbiddenImportLint": forbidden_import_lint,
    "PipelineSchemaDrift": pipeline_schema_drift,
    "WriteTargetBindingHITL": write_target_binding_hitl,
}

__all__ = ["SCORERS", "SKILL_ID"]
