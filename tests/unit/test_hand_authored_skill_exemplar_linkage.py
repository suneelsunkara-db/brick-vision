"""Integration tests for N175 — every hand-authored Layer-0 skill carries
the right ``exemplar_of: meta:<m>/ext:<e>`` field per
``docs/23-databricks-capability-graph.md`` §23.2.6.

Two surfaces are exercised:

1. ``brickvision_runtime.harness.skill_loader.load_skill`` validates
   the field at load time (mandatory, format-checked).
2. ``brickvision_runtime.eval.scorers.capability_graph
   .hand_authored_skill_exemplar_linkage`` is the gate that runs over
   the whole skills directory in CI; this test exercises it against
   the live ``skills/`` directory using the canonical gold set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from brickvision_runtime.eval.scorers.capability_graph import (  # noqa: E402
    hand_authored_skill_exemplar_linkage,
)
from brickvision_runtime.capability_graph.exemplars import (  # noqa: E402
    SkillSchemaError,
    load_skill,
    walk_hand_authored_skills,
)


SKILLS_DIR = REPO_ROOT / "skills"


# --------------------------------------------------------------------- #
# walk_hand_authored_skills + every live SKILL.yaml validates
# --------------------------------------------------------------------- #


def test_walk_finds_all_hand_authored_skills() -> None:
    mapping = walk_hand_authored_skills(SKILLS_DIR)
    assert len(mapping) == 27, sorted(mapping)


def test_every_live_skill_has_well_formed_exemplar_of() -> None:
    """Every entry's value matches ``meta:<m>/ext:<e>``; keys are bare directory names."""
    mapping = walk_hand_authored_skills(SKILLS_DIR)
    for skill_dir, exemplar_ptr in mapping.items():
        assert not skill_dir.startswith("skill:"), (
            f"walk_hand_authored_skills should return directory names, "
            f"not prefixed IDs (got {skill_dir!r})"
        )
        assert exemplar_ptr.startswith("meta:"), (skill_dir, exemplar_ptr)
        meta_part, _, ext_part = exemplar_ptr.partition("/")
        assert meta_part.startswith("meta:"), (skill_dir, exemplar_ptr)
        assert ext_part.startswith("ext:"), (skill_dir, exemplar_ptr)


def test_load_skill_uc_catalog_introspect_exposes_exemplar() -> None:
    """LoadedSkill.exemplar_of returns the §23.2.6 mapping for one canonical skill."""
    sk = load_skill(SKILLS_DIR / "uc.catalog-introspect")
    assert sk.skill_id == "skill:uc.catalog-introspect"
    assert sk.exemplar_of == "meta:unity-catalog-foundation/ext:introspect-catalog-tree"


# --------------------------------------------------------------------- #
# HandAuthoredSkillExemplarLinkage scorer pinned against the live tree
# --------------------------------------------------------------------- #


def test_scorer_passes_against_live_skills_directory() -> None:
    """The live ``skills/`` directory MUST satisfy the scorer at every commit.

    This is the v0.7.7 N175 closing gate. If a hand-authored skill is
    added without a correct ``exemplar_of:`` (or one drifts from the
    §23.2.6 mapping), this test fails immediately.
    """
    observed = walk_hand_authored_skills(SKILLS_DIR)
    result = hand_authored_skill_exemplar_linkage(observed_skill_exemplars=observed)
    assert result.score == 1.0, result.details
    assert not result.reason_codes


def test_scorer_passes_when_extension_set_covers_all_pointers() -> None:
    """When the live capability_graph has all expected extension IDs, scorer still passes."""
    observed = walk_hand_authored_skills(SKILLS_DIR)
    extension_ids = list(observed.values())
    result = hand_authored_skill_exemplar_linkage(
        observed_skill_exemplars=observed,
        extension_ids=extension_ids,
    )
    assert result.score == 1.0, result.details


# --------------------------------------------------------------------- #
# Negative paths — synthetic violations exercise each failure mode
# --------------------------------------------------------------------- #


def test_load_skill_rejects_missing_exemplar_of(tmp_path: Path) -> None:
    """A SKILL.yaml without ``exemplar_of`` is rejected at load time."""
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "SKILL.yaml").write_text(
        'schema_version: "1.0"\n'
        'id: "skill:test.broken"\n'
        'version: "0.1.0"\n'
        'title: "Broken"\n'
        'owner: "team:test"\n'
        'signing_key_id: "uc:secrets:bv/test"\n',
    )
    with pytest.raises(SkillSchemaError, match=r"required field missing: exemplar_of"):
        load_skill(folder)


def test_load_skill_rejects_malformed_exemplar_pointer(tmp_path: Path) -> None:
    """A SKILL.yaml with a malformed ``exemplar_of`` is rejected at load time."""
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "SKILL.yaml").write_text(
        'schema_version: "1.0"\n'
        'id: "skill:test.broken"\n'
        'version: "0.1.0"\n'
        'exemplar_of: "not-a-valid-pointer"\n'
        'title: "Broken"\n'
        'owner: "team:test"\n'
        'signing_key_id: "uc:secrets:bv/test"\n',
    )
    with pytest.raises(SkillSchemaError, match=r"malformed pointer"):
        load_skill(folder)


def test_scorer_flags_drift_from_mapping_table() -> None:
    """A skill pointing at the wrong extension is caught."""
    observed = dict(walk_hand_authored_skills(SKILLS_DIR))
    observed["uc.catalog-introspect"] = "meta:delta-lake/ext:introspect-table-metadata"
    result = hand_authored_skill_exemplar_linkage(observed_skill_exemplars=observed)
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "exemplar_drift" in kinds


def test_scorer_flags_missing_skill_against_gold() -> None:
    """A skill present in §23.2.6 but absent from the observed map is caught."""
    observed = dict(walk_hand_authored_skills(SKILLS_DIR))
    observed.pop("uc.catalog-introspect")
    result = hand_authored_skill_exemplar_linkage(observed_skill_exemplars=observed)
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "missing_exemplar_of" in kinds


def test_scorer_flags_extra_skill_not_in_gold() -> None:
    """The gold set must be updated whenever a new hand-authored skill is added."""
    observed = dict(walk_hand_authored_skills(SKILLS_DIR))
    observed["extra.skill"] = "meta:delta-lake/ext:extra-skill"
    result = hand_authored_skill_exemplar_linkage(observed_skill_exemplars=observed)
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "unexpected_skill_not_in_gold" in kinds


def test_scorer_flags_pointer_not_in_capability_graph() -> None:
    """When the live capability_graph extensions miss a pointer, scorer flags it."""
    observed = walk_hand_authored_skills(SKILLS_DIR)
    extension_ids = [
        ptr for ptr in observed.values()
        if ptr != "meta:unity-catalog-foundation/ext:introspect-catalog-tree"
    ]
    result = hand_authored_skill_exemplar_linkage(
        observed_skill_exemplars=observed,
        extension_ids=extension_ids,
    )
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "exemplar_not_in_capability_graph" in kinds
