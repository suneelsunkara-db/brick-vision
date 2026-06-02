"""Mechanical (DAG) skill: skill:uc.grant-introspect (auto-generated)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG
# bv:templated:end id=imports


# bv:templated:start id=dag
def build_dag() -> DAG:
    return (
        DAG(name="skill:uc.grant-introspect")
        .step(
            id="list_grants",
            tool="tool:uc.list_grants_full",
            inputs={"catalog_filter": "{catalog_filter}"},
        )
        .step(
            id="build_claims",
            kind="pure_python",
            function="build_grant_claims",
            inputs={"grants": "$list_grants.results"},
        )
        .step(
            id="emit_claims",
            tool="tool:kg.emit_claims",
            inputs={"claims": "$build_claims.claims"},
        )
    )
# bv:templated:end id=dag


# bv:templated:start id=skill
SKILL = Skill.mechanical(
    id="skill:uc.grant-introspect",
    version="0.1.0",
    dag=build_dag(),
    constitutional=["no.write.to.uc"],
)
# bv:templated:end id=skill
