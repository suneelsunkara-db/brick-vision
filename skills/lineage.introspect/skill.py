"""Mechanical (DAG) skill: skill:lineage.introspect (auto-generated)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG
# bv:templated:end id=imports


# bv:templated:start id=dag
def build_dag() -> DAG:
    return (
        DAG(name="skill:lineage.introspect")
        .step(
            id="fetch_lineage_rows",
            tool="tool:lineage.read_table_lineage",
            inputs={"since_ts": "{since_ts}", "catalog_filter": "{catalog_filter}"},
        )
        .step(
            id="build_claims",
            kind="pure_python",
            function="build_lineage_claims",
            inputs={"rows": "$fetch_lineage_rows.results"},
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
    id="skill:lineage.introspect",
    version="0.1.0",
    dag=build_dag(),
    constitutional=["no.write.to.uc", "no.cross.workspace.read"],
)
# bv:templated:end id=skill
