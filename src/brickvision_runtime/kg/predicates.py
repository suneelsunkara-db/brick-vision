"""N112 — closed Predicate + EntityKind vocabularies.

Per [`docs/04-schemas.md`](../../../docs/04-schemas.md) §6.4.4 the
knowledge graph uses **closed enums** for predicate and entity
kind so the extractor's structured output can be validated
mechanically and the `subject_card_materializer` (N114) can render
``card_text`` deterministically via per-predicate natural-language
templates.

This module ships:

- ``EntityKind``               — closed enum of entity kinds.
- ``Predicate``                — closed enum of supported predicates.
- ``PredicateMeta``            — per-predicate metadata
  (``canonical_priority`` + ``natural_language_template``).
- ``predicate_meta(predicate)``— lookup helper.
- ``render_card_segment(...)`` — deterministic template renderer.

Adding a new predicate requires:

1. Adding the row to §6.4.4.
2. Adding the enum member here.
3. Adding the metadata to ``_PREDICATE_META`` (with a non-empty
   template + a unique priority within its semantic group).
4. The ``test_v0_7_6_1_predicates.py`` round-trip will fail until
   all three are in place.
"""

from __future__ import annotations

import dataclasses
import re
import string
from enum import Enum
from typing import Any


class EntityKind(str, Enum):
    """Every Subject in the KG carries one ``EntityKind``."""

    WORKSPACE = "WORKSPACE"
    CATALOG = "CATALOG"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    PRINCIPAL = "PRINCIPAL"  # users / SPs / groups
    DOCUMENT = "DOCUMENT"
    SKILL = "SKILL"
    BUILD = "BUILD"
    CONCEPT = "CONCEPT"  # vocabulary-discipline-aligned domain concept


class Predicate(str, Enum):
    """Closed predicate vocabulary."""

    # Read-side coverage / quality.
    WORKSPACE_COVERAGE_BELIEF = "WORKSPACE_COVERAGE_BELIEF"
    DELTA_FILE_COUNT = "DELTA_FILE_COUNT"
    DELTA_DATA_SKIPPING_BYTES = "DELTA_DATA_SKIPPING_BYTES"
    LIQUID_CLUSTERING_KEY_PRESENT = "LIQUID_CLUSTERING_KEY_PRESENT"
    LAYOUT_RECOMMENDATION = "LAYOUT_RECOMMENDATION"

    # Grants + privileges.
    HAS_GRANT = "HAS_GRANT"
    LEAST_PRIVILEGE_RECOMMENDATION = "LEAST_PRIVILEGE_RECOMMENDATION"

    # Mentions + content (the docs.lookup writers).
    MENTIONS = "MENTIONS"
    CONTENT = "CONTENT"

    # Build pipeline auditing.
    BUILD_STEP_COMPLETED = "BUILD_STEP_COMPLETED"
    HITL_APPROVED = "HITL_APPROVED"


@dataclasses.dataclass(frozen=True, slots=True)
class PredicateMeta:
    """Per-predicate metadata used by the materializer + retriever."""

    canonical_priority: int  # lower = higher priority in subject card
    natural_language_template: str  # uses ``{value}`` and ``{subject}`` placeholders
    description: str = ""


_PREDICATE_META: dict[Predicate, PredicateMeta] = {
    Predicate.WORKSPACE_COVERAGE_BELIEF: PredicateMeta(
        canonical_priority=10,
        natural_language_template=(
            "BrickVision believes coverage of {subject} is {value}."
        ),
        description="High-level workspace coverage belief.",
    ),
    Predicate.DELTA_FILE_COUNT: PredicateMeta(
        canonical_priority=20,
        natural_language_template="{subject} has {value} small files.",
    ),
    Predicate.DELTA_DATA_SKIPPING_BYTES: PredicateMeta(
        canonical_priority=21,
        natural_language_template="Data skipping on {subject} skips {value} bytes.",
    ),
    Predicate.LIQUID_CLUSTERING_KEY_PRESENT: PredicateMeta(
        canonical_priority=22,
        natural_language_template="{subject} has liquid clustering: {value}.",
    ),
    Predicate.LAYOUT_RECOMMENDATION: PredicateMeta(
        canonical_priority=23,
        natural_language_template="Recommended layout for {subject}: {value}.",
    ),
    Predicate.HAS_GRANT: PredicateMeta(
        canonical_priority=30,
        natural_language_template="{subject} has grant: {value}.",
    ),
    Predicate.LEAST_PRIVILEGE_RECOMMENDATION: PredicateMeta(
        canonical_priority=31,
        natural_language_template="Least-privilege grant for {subject}: {value}.",
    ),
    Predicate.MENTIONS: PredicateMeta(
        canonical_priority=40,
        natural_language_template="{subject} mentions: {value}.",
    ),
    Predicate.CONTENT: PredicateMeta(
        canonical_priority=99,
        natural_language_template="{subject} contains text content: {value}.",
        description=(
            "Document chunk text — never rendered into the subject card"
            " directly; the materializer skips this predicate (priority 99"
            " is the materializer's exclusion threshold)."
        ),
    ),
    Predicate.BUILD_STEP_COMPLETED: PredicateMeta(
        canonical_priority=50,
        natural_language_template="Build step completed: {value}.",
    ),
    Predicate.HITL_APPROVED: PredicateMeta(
        canonical_priority=51,
        natural_language_template="HITL approved: {value}.",
    ),
}


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def predicate_meta(p: Predicate | str) -> PredicateMeta:
    """Look up metadata for a predicate.

    Raises ``KeyError`` for unknown predicates so the materializer
    can surface ``KG_PREDICATE_UNKNOWN`` rather than silently
    skipping (P7 — never silent).
    """

    if isinstance(p, str):
        try:
            key: Predicate = Predicate(p)
        except ValueError as exc:
            raise KeyError(f"unknown predicate: {p!r}") from exc
    else:
        key = p
    if key not in _PREDICATE_META:
        raise KeyError(f"unknown predicate: {key}")
    return _PREDICATE_META[key]


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def template_placeholders(template: str) -> set[str]:
    """Return every ``{placeholder}`` name in ``template``."""

    return set(_PLACEHOLDER_RE.findall(template))


def render_card_segment(
    *,
    predicate: Predicate | str,
    subject: str,
    value: Any,
) -> str:
    """Render one card segment with the predicate's NL template.

    Missing placeholders raise ``KeyError`` (P7); extra format
    args are ignored. Values are coerced to ``str`` to keep the
    output deterministic.
    """

    meta = predicate_meta(predicate)
    template = meta.natural_language_template
    placeholders = template_placeholders(template)

    bag: dict[str, str] = {}
    if "subject" in placeholders:
        bag["subject"] = str(subject)
    if "value" in placeholders:
        bag["value"] = _format_value(value)

    missing = placeholders - bag.keys()
    if missing:
        raise KeyError(
            f"render_card_segment missing placeholders {sorted(missing)}"
            f" for predicate {predicate}"
        )

    formatter = string.Formatter()
    return formatter.vformat(template, args=(), kwargs=bag)


def _format_value(value: Any) -> str:
    if isinstance(value, dict):
        # Ordered, JSON-ish for stability without importing json.
        return ", ".join(f"{k}={value[k]}" for k in sorted(value))
    return str(value)


# ---------------------------------------------------------------------------
# Sanity check exposed for tests.
# ---------------------------------------------------------------------------


def all_predicates_have_metadata() -> tuple[bool, list[str]]:
    """Return ``(ok, missing)`` so the test suite can assert
    every enum member is documented."""

    missing = [p.value for p in Predicate if p not in _PREDICATE_META]
    return (not missing, missing)


__all__ = [
    "EntityKind",
    "Predicate",
    "PredicateMeta",
    "all_predicates_have_metadata",
    "predicate_meta",
    "render_card_segment",
    "template_placeholders",
]
