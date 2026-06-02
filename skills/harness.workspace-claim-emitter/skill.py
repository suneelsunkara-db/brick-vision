"""Mechanical (DAG) skill: skill:harness.workspace-claim-emitter (auto-generated)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG
# bv:templated:end id=imports


# bv:templated:start id=dag
def build_dag() -> DAG:
    return (
        DAG(name="skill:harness.workspace-claim-emitter")
    )
# bv:templated:end id=dag


# bv:templated:start id=skill
SKILL = Skill.mechanical(
    id="skill:harness.workspace-claim-emitter",
    version="0.1.0",
    dag=build_dag(),
    constitutional=["no.write.to.uc", "must.emit.question.on.partial.failure", "must.respect.budget.caps.per.skill"],
)
# bv:templated:end id=skill
