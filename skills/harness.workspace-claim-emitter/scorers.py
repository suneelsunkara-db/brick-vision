"""Custom scorers for skill:harness.workspace-claim-emitter (auto-generated; bind to `evals/` suite)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
# bv:templated:end id=imports



# bv:templated:start id=scorer:claimcountassertion
@register_scorer(skill_id="skill:harness.workspace-claim-emitter", name="ClaimCountAssertion")
def score_claimcountassertion(prediction, ground_truth, context):
    """ClaimCountAssertion scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for ClaimCountAssertion — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:claimcountassertion


# bv:templated:start id=scorer:freshnessbeliefmonotonicity
@register_scorer(skill_id="skill:harness.workspace-claim-emitter", name="FreshnessBeliefMonotonicity")
def score_freshnessbeliefmonotonicity(prediction, ground_truth, context):
    """FreshnessBeliefMonotonicity scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for FreshnessBeliefMonotonicity — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:freshnessbeliefmonotonicity


# bv:templated:start id=scorer:questionemissiononfailure
@register_scorer(skill_id="skill:harness.workspace-claim-emitter", name="QuestionEmissionOnFailure")
def score_questionemissiononfailure(prediction, ground_truth, context):
    """QuestionEmissionOnFailure scorer (stub — wired by `stage:agent-evaluate`)."""
    raise NotImplementedError(
        "Stub scorer for QuestionEmissionOnFailure — populate via `stage:agent-evaluate`."
    )
# bv:templated:end id=scorer:questionemissiononfailure


