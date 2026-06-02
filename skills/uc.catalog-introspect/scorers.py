"""Custom scorers for skill:uc.catalog-introspect (auto-generated; bind to `evals/` suite)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
# bv:templated:end id=imports



# bv:templated:start id=scorer:claimcountassertion
@register_scorer(skill_id="skill:uc.catalog-introspect", name="ClaimCountAssertion")
def score_claimcountassertion(prediction, ground_truth, context):
    """ClaimCountAssertion scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for ClaimCountAssertion — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:claimcountassertion


# bv:templated:start id=scorer:claimshapevalidator
@register_scorer(skill_id="skill:uc.catalog-introspect", name="ClaimShapeValidator")
def score_claimshapevalidator(prediction, ground_truth, context):
    """ClaimShapeValidator scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for ClaimShapeValidator — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:claimshapevalidator


# bv:templated:start id=scorer:idempotence
@register_scorer(skill_id="skill:uc.catalog-introspect", name="Idempotence")
def score_idempotence(prediction, ground_truth, context):
    """Idempotence scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Idempotence — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:idempotence


