"""Mechanical (DAG) skill: skill:delta.table-introspect (auto-generated)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG
# bv:templated:end id=imports


# bv:templated:start id=dag
def build_dag() -> DAG:
    return (
        DAG(name="skill:delta.table-introspect")
        .step(
            id="describe_extended",
            tool="tool:delta.describe_extended",
            inputs={"table_fqn": "{target}"},
        )
        .step(
            id="describe_history",
            tool="tool:delta.describe_history",
            inputs={"table_fqn": "{target}"},
        )
        .step(
            id="po_history",
            tool="tool:delta.predictive_optimization_history",
            inputs={"table_fqn": "{target}"},
        )
        .step(
            id="build_claims",
            kind="pure_python",
            function="build_table_introspect_claims",
            inputs={"described": "$describe_extended.result", "history": "$describe_history.results", "po": "$po_history.results"},
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
    id="skill:delta.table-introspect",
    version="0.1.0",
    dag=build_dag(),
    constitutional=["no.write.to.uc", "no.cross.workspace.read"],
)
# bv:templated:end id=skill
