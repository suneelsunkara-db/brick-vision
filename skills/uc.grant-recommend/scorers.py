"""Custom scorers for skill:uc.grant-recommend (auto-generated; bind to `evals/` suite)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
# bv:templated:end id=imports



# bv:templated:start id=scorer:correctness
@register_scorer(skill_id="skill:uc.grant-recommend", name="Correctness")
def score_correctness(prediction, ground_truth, context):
    """Correctness scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Correctness — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:correctness


# bv:templated:start id=scorer:guidelines_least_privilege
@register_scorer(skill_id="skill:uc.grant-recommend", name="Guidelines:least-privilege")
def score_guidelines_least_privilege(prediction, ground_truth, context):
    """Guidelines:least-privilege scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:least-privilege — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_least_privilege


# bv:templated:start id=scorer:guidelines_no_redundant_grants
@register_scorer(skill_id="skill:uc.grant-recommend", name="Guidelines:no-redundant-grants")
def score_guidelines_no_redundant_grants(prediction, ground_truth, context):
    """Guidelines:no-redundant-grants scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:no-redundant-grants — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_no_redundant_grants


# bv:templated:start id=scorer:guidelines_must_cite_evidence
@register_scorer(skill_id="skill:uc.grant-recommend", name="Guidelines:must-cite-evidence")
def score_guidelines_must_cite_evidence(prediction, ground_truth, context):
    """Guidelines:must-cite-evidence scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:must-cite-evidence — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_must_cite_evidence


# bv:templated:start id=scorer:guidelines_no_fabricated_securables
@register_scorer(skill_id="skill:uc.grant-recommend", name="Guidelines:no-fabricated-securables")
def score_guidelines_no_fabricated_securables(prediction, ground_truth, context):
    """Guidelines:no-fabricated-securables scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Guidelines:no-fabricated-securables — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:guidelines_no_fabricated_securables


# bv:templated:start id=scorer:retrievalgroundedness
@register_scorer(skill_id="skill:uc.grant-recommend", name="RetrievalGroundedness")
def score_retrievalgroundedness(prediction, ground_truth, context):
    """RetrievalGroundedness scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for RetrievalGroundedness — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:retrievalgroundedness


# bv:templated:start id=scorer:safety
@register_scorer(skill_id="skill:uc.grant-recommend", name="Safety")
def score_safety(prediction, ground_truth, context):
    """Safety scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Safety — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:safety


