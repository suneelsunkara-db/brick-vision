"""Per-skill scorers for ``skill:docs.lookup``.

Wraps the four golden-set scorers referenced in ``SKILL.yaml`` so the
skill folder is self-contained for round-trip B (extract → transpile →
diff). The implementations live in ``brickvision_runtime.eval.scorers``
to keep the core scorer registry as the single source of truth.
"""

from __future__ import annotations

from brickvision_runtime.eval.scorers import register_scorer
from brickvision_runtime.eval.scorers.kg_layer import (
    docs_lookup_freshness as _docs_lookup_freshness,
    docs_lookup_idempotence as _docs_lookup_idempotence,
    mention_extraction_precision as _mention_precision,
    mention_extraction_recall as _mention_recall,
    vocabulary_gap_discipline as _vocab_gap_discipline,
)


@register_scorer(skill_id="skill:docs.lookup", name="DocsLookupIdempotence")
def docs_lookup_idempotence(*args, **kwargs):
    return _docs_lookup_idempotence(*args, **kwargs)


@register_scorer(skill_id="skill:docs.lookup", name="MentionExtractionPrecision")
def mention_extraction_precision(*args, **kwargs):
    return _mention_precision(*args, **kwargs)


@register_scorer(skill_id="skill:docs.lookup", name="MentionExtractionRecall")
def mention_extraction_recall(*args, **kwargs):
    return _mention_recall(*args, **kwargs)


@register_scorer(skill_id="skill:docs.lookup", name="VocabularyGapDiscipline")
def vocabulary_gap_discipline(*args, **kwargs):
    return _vocab_gap_discipline(*args, **kwargs)


@register_scorer(skill_id="skill:docs.lookup", name="DocsLookupFreshness")
def docs_lookup_freshness(*args, **kwargs):
    return _docs_lookup_freshness(*args, **kwargs)


__all__ = [
    "docs_lookup_freshness",
    "docs_lookup_idempotence",
    "mention_extraction_precision",
    "mention_extraction_recall",
    "vocabulary_gap_discipline",
]
