"""N175 (v0.7.7 MVI): SKILL.yaml v1.0 loader + schema validator.

Reads a skill folder, validates `schema_version: "1.0"`, surfaces a typed
`LoadedSkill` aggregating SKILL.yaml + the optional canonical DESIGN.yaml.

v0.7.7 (N175): the ``exemplar_of: meta:<m>/ext:<e>`` field is now
**mandatory** on every hand-authored Layer-0 skill (per
``docs/23-databricks-capability-graph.md`` §23.2.6 — every Layer-0 skill
must declare its parent extension in the Capability Graph). The format
``meta:<meta-skill-id>/ext:<extension-id>`` is enforced here so the
constraint surfaces at load time rather than waiting for the
``HandAuthoredSkillExemplarLinkage()`` scorer to flag it later.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
    _HAS_PYYAML = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _HAS_PYYAML = False

from brickvision_runtime._vendor import minyaml


def _load_skill_yaml(folder: Path) -> dict[str, Any]:
    """Load ``<folder>/SKILL.yaml`` as a plain dict (no IR derivation)."""

    skill_yaml = folder / "SKILL.yaml"
    if not skill_yaml.exists():
        raise FileNotFoundError(f"no SKILL.yaml in {folder}")
    text = skill_yaml.read_text()
    raw: dict[str, Any] = (
        yaml.safe_load(text) if _HAS_PYYAML else minyaml.safe_load(text)
    )
    return {
        "schema_version": raw.get("schema_version", "1.0"),
        "id": raw.get("id"),
        "version": raw.get("version"),
        "title": raw.get("title"),
        "owner": raw.get("owner"),
        "signing_key_id": raw.get("signing_key_id"),
        "exemplar_of": raw.get("exemplar_of"),
        "capability_links": dict(raw.get("capability_links", {})),
        "kind": (
            "harness" if str(raw.get("id", "")).startswith("skill:harness.") else "skill"
        ),
        "requires": dict(raw.get("requires", {})),
    }


class SkillSchemaError(ValueError):
    """SKILL.yaml schema mismatch (unknown version, missing required fields)."""


_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "version",
    "title",
    "owner",
    "signing_key_id",
    # v0.7.7 N175: every hand-authored Layer-0 skill MUST link to its
    # parent extension in the Capability Graph. The mapping table lives
    # in ``docs/23-databricks-capability-graph.md`` §23.2.6.
    "exemplar_of",
)

# Format: ``meta:<meta-skill-id>/ext:<extension-id>``. The two IDs use
# kebab-case slugs (lowercase letters + digits + hyphen), at least two
# characters each. Matches the same shape as
# ``capability_graph/eval/scorers/capability_graph.py::_EXEMPLAR_PTR_RE``
# so behaviour is consistent between load-time validation and scorer
# evaluation.
_EXEMPLAR_PTR_RE = re.compile(
    r"^meta:[a-z0-9][a-z0-9_-]*[a-z0-9]/ext:[a-z0-9][a-z0-9_-]*[a-z0-9]$",
)


@dataclasses.dataclass(frozen=True)
class LoadedSkill:
    folder: Path
    skill_id: str
    version: str
    ir: dict[str, Any]

    @property
    def kind(self) -> str:
        return self.ir.get("kind", "skill")

    @property
    def constitutional(self) -> tuple[str, ...]:
        return tuple(self.ir.get("requires", {}).get("constitutional", ()))

    @property
    def exemplar_of(self) -> str:
        """The parent extension pointer (``meta:<m>/ext:<e>``).

        Always non-empty for v0.7.7+ SKILL.yaml files (validated at
        load time per N175). The value is the link from the
        hand-authored Layer-0 skill into the Capability Graph's
        ``Extension`` row that this skill exemplifies.
        """
        return str(self.ir["exemplar_of"])


def load_skill(folder: str | Path) -> LoadedSkill:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"skill folder not found: {folder}")
    ir = _load_skill_yaml(folder)
    schema_version = ir.get("schema_version")
    if schema_version != "1.0":
        raise SkillSchemaError(
            f"unsupported SKILL.yaml schema_version={schema_version!r}; expected '1.0'",
        )
    for field in _REQUIRED_FIELDS:
        if not ir.get(field):
            raise SkillSchemaError(f"required field missing: {field}")
    exemplar_ptr = str(ir["exemplar_of"])
    if not _EXEMPLAR_PTR_RE.match(exemplar_ptr):
        raise SkillSchemaError(
            f"exemplar_of has malformed pointer {exemplar_ptr!r}; expected "
            "'meta:<meta-skill-id>/ext:<extension-id>' per "
            "docs/23-databricks-capability-graph.md §23.2.6",
        )
    return LoadedSkill(
        folder=folder,
        skill_id=ir["id"],
        version=ir["version"],
        ir=ir,
    )


def walk_hand_authored_skills(skills_dir: str | Path) -> Mapping[str, str]:
    """Walk ``skills/`` and return a ``directory_name -> exemplar_of`` mapping.

    Used by ``HandAuthoredSkillExemplarLinkage()`` (the scorer that
    asserts every hand-authored Layer-0 skill carries the right
    ``exemplar_of`` pointer per the §23.2.6 mapping table).

    Output key convention: this returns mapping keys in the form of
    the **skill's directory name** (e.g. ``ml.serve-deploy``), not the
    SKILL.yaml ``id:`` field which carries a ``skill:`` prefix
    (``skill:ml.serve-deploy``). This matches the gold-set contract
    documented on
    :class:`brickvision_runtime.eval.gold.capability_graph
    .HandAuthoredExemplarLinkGoldRow.skill_id` ("the SKILL.yaml's
    directory name").

    Raises :class:`SkillSchemaError` on the first invalid SKILL.yaml,
    which is the right behaviour for a CI gate (one bad skill is the
    whole problem) — callers that need a partial walk can catch and
    continue.

    Skips:
      * non-directory entries directly under ``skills_dir``
      * subdirectories without a ``SKILL.yaml`` file
        (e.g. ``skills/.canonical/``, ``skills/__pycache__/``)
    """
    root = Path(skills_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"skills directory not found: {root}")
    out: dict[str, str] = {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        if not (folder / "SKILL.yaml").is_file():
            continue
        skill = load_skill(folder)
        out[folder.name] = skill.exemplar_of
    return out


__all__ = [
    "LoadedSkill",
    "SkillSchemaError",
    "load_skill",
    "walk_hand_authored_skills",
]
