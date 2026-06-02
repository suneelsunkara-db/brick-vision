"""Scorer registry for ``skill:delta.pyspark-transform``.

The actual scorers live in
``brickvision_runtime.eval.scorers.write_side``; this module just
re-exports them under the skill id so the harness loader picks
them up the same way docs.lookup does.
"""

from __future__ import annotations

from brickvision_runtime.eval.scorers.write_side import (
    forbidden_import_lint,
    pipeline_schema_drift,
    transform_parse_validity,
    write_target_binding_hitl,
)

SKILL_ID = "skill:delta.pyspark-transform"

SCORERS = {
    "TransformParseValidity": transform_parse_validity,
    "ForbiddenImportLint": forbidden_import_lint,
    "PipelineSchemaDrift": pipeline_schema_drift,
    "WriteTargetBindingHITL": write_target_binding_hitl,
}

__all__ = ["SCORERS", "SKILL_ID"]
