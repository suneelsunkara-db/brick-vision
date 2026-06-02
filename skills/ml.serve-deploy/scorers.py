"""Scorer registry for ``skill:ml.serve-deploy``."""

from __future__ import annotations

from brickvision_runtime.eval.scorers.write_side import (
    serve_deploy_alias_invariant,
    serve_deploy_hitl_enforced,
)

SKILL_ID = "skill:ml.serve-deploy"

SCORERS = {
    "ServeDeployAliasInvariant": serve_deploy_alias_invariant,
    "ServeDeployHITLEnforced": serve_deploy_hitl_enforced,
}

__all__ = ["SCORERS", "SKILL_ID"]
