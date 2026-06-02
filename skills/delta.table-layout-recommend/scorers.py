"""Custom scorers for skill:delta.table-layout-recommend (auto-generated; bind to `evals/` suite)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
# bv:templated:end id=imports



# bv:templated:start id=scorer:correctness
@register_scorer(skill_id="skill:delta.table-layout-recommend", name="Correctness")
def score_correctness(prediction, ground_truth, context):
    """Correctness scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Correctness — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:correctness


# bv:templated:start id=scorer:guidelines_no_tiny_table_partition
@register_scorer(skill_id="skill:delta.table-layout-recommend", name="Guidelines:no-tiny-table-partition")
def score_guidelines_no_tiny_table_partition(prediction, ground_truth, context):
    """Guidelines:no-tiny-table-partition scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:no-tiny-table-partition — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_no_tiny_table_partition


# bv:templated:start id=scorer:guidelines_must_cite_evidence
@register_scorer(skill_id="skill:delta.table-layout-recommend", name="Guidelines:must-cite-evidence")
def score_guidelines_must_cite_evidence(prediction, ground_truth, context):
    """Guidelines:must-cite-evidence scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:must-cite-evidence — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_must_cite_evidence


# bv:templated:start id=scorer:retrievalgroundedness
@register_scorer(skill_id="skill:delta.table-layout-recommend", name="RetrievalGroundedness")
def score_retrievalgroundedness(prediction, ground_truth, context):
    """RetrievalGroundedness scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for RetrievalGroundedness — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:retrievalgroundedness


# bv:templated:start id=scorer:safety
@register_scorer(skill_id="skill:delta.table-layout-recommend", name="Safety")
def score_safety(prediction, ground_truth, context):
    """Safety scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Safety — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:safety


