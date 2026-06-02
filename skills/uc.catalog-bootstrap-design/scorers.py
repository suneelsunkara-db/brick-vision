"""Custom scorers for skill:uc.catalog-bootstrap-design (auto-generated; bind to `evals/` suite)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
# bv:templated:end id=imports



# bv:templated:start id=scorer:correctness
@register_scorer(skill_id="skill:uc.catalog-bootstrap-design", name="Correctness")
def score_correctness(prediction, ground_truth, context):
    """Correctness scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Correctness — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:correctness


# bv:templated:start id=scorer:guidelines_must_cite_evidence
@register_scorer(skill_id="skill:uc.catalog-bootstrap-design", name="Guidelines:must-cite-evidence")
def score_guidelines_must_cite_evidence(prediction, ground_truth, context):
    """Guidelines:must-cite-evidence scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:must-cite-evidence — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_must_cite_evidence


# bv:templated:start id=scorer:guidelines_no_fabricated_securables
@register_scorer(skill_id="skill:uc.catalog-bootstrap-design", name="Guidelines:no-fabricated-securables")
def score_guidelines_no_fabricated_securables(prediction, ground_truth, context):
    """Guidelines:no-fabricated-securables scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:no-fabricated-securables — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_no_fabricated_securables


# bv:templated:start id=scorer:retrievalgroundedness
@register_scorer(skill_id="skill:uc.catalog-bootstrap-design", name="RetrievalGroundedness")
def score_retrievalgroundedness(prediction, ground_truth, context):
    """RetrievalGroundedness scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for RetrievalGroundedness — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:retrievalgroundedness


# bv:templated:start id=scorer:safety
@register_scorer(skill_id="skill:uc.catalog-bootstrap-design", name="Safety")
def score_safety(prediction, ground_truth, context):
    """Safety scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Safety — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:safety


