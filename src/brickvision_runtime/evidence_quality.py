"""Evidence-substrate quality checks for suggestions and skill selection.

These checks are deliberately pure: callers pass active Capability Graph
extension IDs and Workspace Context claim rows. The live Databricks/Lakebase
fetch belongs in scripts or jobs, not in the validator itself.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


@dataclasses.dataclass(frozen=True)
class EvidenceQualityReport:
    """Compact report shape that can be emitted by jobs or rendered in UI."""

    passed: bool
    reason_codes: tuple[str, ...]
    details: dict[str, Any]


def validate_skill_anchor_resolution(
    *,
    skill_exemplars: Mapping[str, str],
    extension_ids: Iterable[str],
    pending_stub_anchors: Iterable[str] = (),
    extension_source_kinds: Mapping[str, Iterable[str]] | None = None,
    require_source_grounding: bool = False,
    grounding_source_kinds: Iterable[str] = ("sdk", "openapi", "docs", "labs", "blog"),
) -> EvidenceQualityReport:
    """Validate that every skill anchor resolves or is explicitly pending.

    ``skill_exemplars`` is the ``skill_dir -> exemplar_of`` mapping from
    ``walk_hand_authored_skills``. ``extension_ids`` must be the active
    Capability Graph snapshot, not a static/gold set.

    When ``require_source_grounding`` is true, each resolved anchor must
    have at least one non-hand-authored source contribution. This catches
    the false-positive case where ``graph_builder`` minted a stub extension
    solely to satisfy ``exemplar_of`` linkage, but the anchor is not yet
    grounded in indexed Databricks docs/API/SDK/labs evidence.
    """

    active_extensions = {str(extension_id) for extension_id in extension_ids}
    pending = {str(anchor) for anchor in pending_stub_anchors}
    grounding_sources = {str(kind) for kind in grounding_source_kinds}
    sources_by_extension = {
        str(extension_id): {str(kind) for kind in kinds}
        for extension_id, kinds in (extension_source_kinds or {}).items()
    }
    missing: list[dict[str, str]] = []
    pending_hits: list[dict[str, str]] = []
    ungrounded: list[dict[str, Any]] = []
    resolved = 0

    for skill_id, anchor in sorted(skill_exemplars.items()):
        anchor = str(anchor)
        if anchor in active_extensions:
            resolved += 1
            source_kinds = sources_by_extension.get(anchor, set())
            is_grounded = bool(source_kinds & grounding_sources)
            if require_source_grounding and not is_grounded:
                ungrounded.append(
                    {
                        "skill_id": str(skill_id),
                        "anchor": anchor,
                        "source_kinds": sorted(source_kinds),
                    }
                )
        elif anchor in pending:
            pending_hits.append({"skill_id": str(skill_id), "anchor": anchor})
        else:
            missing.append({"skill_id": str(skill_id), "anchor": anchor})

    reason_codes: list[str] = []
    if missing:
        reason_codes.append("HAND_AUTHORED_SKILL_ANCHOR_NOT_IN_ACTIVE_GRAPH")
    if ungrounded:
        reason_codes.append("HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED")

    return EvidenceQualityReport(
        passed=not missing and not ungrounded,
        reason_codes=tuple(reason_codes),
        details={
            "skill_count": len(skill_exemplars),
            "resolved_count": resolved,
            "pending_stub_count": len(pending_hits),
            "missing_count": len(missing),
            "ungrounded_count": len(ungrounded),
            "missing": missing,
            "pending_stubs": pending_hits,
            "ungrounded": ungrounded,
        },
    )


def validate_workspace_claim_quality(
    *,
    claims: Sequence[Mapping[str, Any]],
    required_profile_predicates: Iterable[str] = (
        "HAS_COLUMN",
        "ROW_COUNT",
        "NULL_COUNT",
        "DISTINCT_COUNT",
        "GRAIN_CHECK",
    ),
) -> EvidenceQualityReport:
    """Validate whether Workspace Context is rich enough for build suggestions."""

    by_kind: Counter[str] = Counter()
    by_predicate: Counter[str] = Counter()
    by_source_skill: Counter[str] = Counter()
    subject_kind_mismatches: list[dict[str, str]] = []

    for claim in claims:
        subject = str(claim.get("subject", ""))
        kind = str(claim.get("subject_kind", ""))
        predicate = str(claim.get("predicate", ""))
        source_skill = str(claim.get("source_skill_id", ""))
        by_kind[kind] += 1
        by_predicate[predicate] += 1
        by_source_skill[source_skill] += 1

        expected_kind = _expected_kind_from_subject(subject)
        if expected_kind and kind != expected_kind:
            subject_kind_mismatches.append(
                {
                    "subject": subject,
                    "observed_subject_kind": kind,
                    "expected_subject_kind": expected_kind,
                }
            )

    profile_predicates = {str(predicate) for predicate in required_profile_predicates}
    observed_profile_predicates = sorted(profile_predicates & set(by_predicate))
    missing_profile_predicates = sorted(profile_predicates - set(by_predicate))

    reason_codes: list[str] = []
    if subject_kind_mismatches:
        reason_codes.append("WORKSPACE_CLAIM_SUBJECT_KIND_MISMATCH")
    if missing_profile_predicates:
        reason_codes.append("WORKSPACE_PROFILE_CLAIMS_MISSING")

    return EvidenceQualityReport(
        passed=not reason_codes,
        reason_codes=tuple(reason_codes),
        details={
            "claim_count": len(claims),
            "by_kind": dict(sorted(by_kind.items())),
            "by_predicate": dict(sorted(by_predicate.items())),
            "by_source_skill": dict(sorted(by_source_skill.items())),
            "subject_kind_mismatch_count": len(subject_kind_mismatches),
            "subject_kind_mismatches": subject_kind_mismatches[:50],
            "observed_profile_predicates": observed_profile_predicates,
            "missing_profile_predicates": missing_profile_predicates,
        },
    )


def _expected_kind_from_subject(subject: str) -> str | None:
    prefix, sep, _ = subject.partition(":")
    if not sep:
        return None
    mapping = {
        "catalog": "CATALOG",
        "schema": "SCHEMA",
        "table": "TABLE",
        "view": "VIEW",
        "function": "FUNCTION",
        "volume": "VOLUME",
    }
    return mapping.get(prefix)


__all__ = [
    "EvidenceQualityReport",
    "validate_skill_anchor_resolution",
    "validate_workspace_claim_quality",
]
