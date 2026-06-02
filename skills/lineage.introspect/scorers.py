"""Custom scorers for skill:lineage.introspect (auto-generated; bind to `evals/` suite)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
# bv:templated:end id=imports



# bv:templated:start id=scorer:claimcountassertion
@register_scorer(skill_id="skill:lineage.introspect", name="ClaimCountAssertion")
def score_claimcountassertion(prediction, ground_truth, context):
    """ClaimCountAssertion scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for ClaimCountAssertion — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:claimcountassertion


# bv:templated:start id=scorer:idempotence
@register_scorer(skill_id="skill:lineage.introspect", name="Idempotence")
def score_idempotence(prediction, ground_truth, context):
    """Idempotence scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for Idempotence — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:idempotence


