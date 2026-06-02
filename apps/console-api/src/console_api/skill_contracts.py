"""Shared Skill Builder contract and readiness primitives."""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import yaml


FAMILY_SKILLS: dict[str, tuple[str, ...]] = {
    "SQL": ("skill:delta.sql-transform", "skill:databricks.statement-execute"),
    "PySpark": (
        "skill:delta.pyspark-transform",
        "skill:delta.pyspark-task-plan",
        "skill:lakeflow.jobs-run-submit",
    ),
    "Jobs": ("skill:lakeflow.jobs-run-submit",),
    "ML": (
        "skill:ml.problem-select",
        "skill:ml.feature-readiness",
        "skill:ml.strategy-plan",
        "skill:ml.model-family-select",
        "skill:ml.training-backend-probe",
        "skill:ml.training-backend-select",
        "skill:ml.training-artifact-plan",
        "skill:ml.training-task-plan",
        "skill:ml.api-plan-bind",
        "skill:ml.train-evaluate-register",
        "skill:ml.assign-alias",
    ),
    "Migration": (
        "skill:migration.lakebridge-sql-transpile",
        "skill:migration.lakebridge-support-matrix",
        "skill:migration.lakebridge-assess",
    ),
    "Code Convert": ("skill:migration.lakebridge-code-convert",),
    "AI": (),
}

TOOL_RUNTIME_MODULES: dict[str, tuple[str, ...]] = {
    "tool:data_pipeline.submit_sql": ("brickvision_runtime.data_pipeline:run_sql_transform",),
    "tool:data_pipeline.submit_pyspark_job": ("brickvision_runtime.data_pipeline:run_pyspark_transform",),
    "tool:uc.bindings_check": ("brickvision_runtime.install.preflight.uc_bindings:bindings_check",),
    "tool:uc.list_catalogs": ("brickvision_runtime.tools.uc:list_catalogs",),
    "tool:uc.list_schemas": ("brickvision_runtime.tools.uc:list_schemas",),
    "tool:uc.list_tables": ("brickvision_runtime.tools.uc:list_tables",),
    "tool:uc.list_views": ("brickvision_runtime.tools.uc:list_views",),
    "tool:uc.list_volumes": ("brickvision_runtime.tools.uc:list_volumes",),
    "tool:uc.list_functions": ("brickvision_runtime.tools.uc:list_functions",),
    "tool:ml.run_training_job": (
        "brickvision_runtime.ml.databricks_training:run_databricks_training_job",
    ),
    "tool:ml.register_model": ("brickvision_runtime.ml:register_model",),
    "tool:ml.set_registered_model_alias": ("brickvision_runtime.ml.alias_assigner:assign_alias",),
    "tool:docs.fetch_url": ("file:skills/docs.lookup/tools.py:fetch_url",),
    "tool:kg.emit_claims": ("brickvision_runtime.kg.claims:emit_claims",),
    "tool:hitl.request_approval": ("brickvision_runtime.hitl:request_approval",),
}

EXECUTION_ADAPTER_GAPS: dict[str, str] = {}


@dataclasses.dataclass(frozen=True)
class SkillContract:
    skill_id: str
    title: str
    version: str
    exemplar_of: str
    category: str
    description: str
    when_to_use: tuple[str, ...]
    triggers: tuple[str, ...]
    model_role: str
    tools: tuple[str, ...]
    required_skills: tuple[str, ...]
    inputs: tuple[dict[str, Any], ...]
    outputs: tuple[dict[str, Any], ...]
    scorers: tuple[str, ...]
    runtime: str
    on_failure: str
    owner: str
    skill_dir: Path
    skill_yaml: Path
    skill_py: Path

    @property
    def runner_name(self) -> str:
        slug = self.skill_id.split(":", 1)[-1].replace(".", "_").replace("-", "_")
        return f"run_{slug}"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "title": self.title,
            "version": self.version,
            "exemplar_of": self.exemplar_of,
            "category": self.category,
            "description": self.description,
            "when_to_use": list(self.when_to_use),
            "triggers": list(self.triggers),
            "model_role": self.model_role,
            "tools": list(self.tools),
            "required_skills": list(self.required_skills),
            "inputs": [dict(item) for item in self.inputs],
            "outputs": [dict(item) for item in self.outputs],
            "scorers": list(self.scorers),
            "runtime": self.runtime,
            "on_failure": self.on_failure,
            "owner": self.owner,
            "skill_dir": str(self.skill_dir.relative_to(repo_root())),
            "runner_name": self.runner_name,
        }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def skills_root() -> Path:
    return repo_root() / "skills"


def list_skill_contracts() -> list[SkillContract]:
    contracts: list[SkillContract] = []
    for skill_yaml in sorted(skills_root().glob("*/SKILL.yaml"), key=lambda item: item.parent.name):
        contract = load_skill_contract_from_path(skill_yaml)
        if contract:
            contracts.append(contract)
    return contracts


def load_skill_contract(skill_id: str) -> SkillContract | None:
    for contract in list_skill_contracts():
        if contract.skill_id == skill_id:
            return contract
    return None


def load_skill_contract_from_path(skill_yaml: Path) -> SkillContract | None:
    try:
        raw = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    skill_id = _string(raw.get("id"))
    if not skill_id:
        return None
    requires = _dict(raw.get("requires"))
    load_signal = _dict(raw.get("load_signal"))
    eval_section = _dict(raw.get("eval"))
    execution = _dict(raw.get("execution"))
    return SkillContract(
        skill_id=skill_id,
        title=_string(raw.get("title")) or skill_id,
        version=_string(raw.get("version")),
        exemplar_of=_string(raw.get("exemplar_of")),
        category=_category(skill_id),
        description=_string(raw.get("description")),
        when_to_use=tuple(_string_list(raw.get("when_to_use"))),
        triggers=tuple(_string_list(load_signal.get("triggers"))),
        model_role=_string(requires.get("model_role")),
        tools=tuple(_string_list(requires.get("tools"))),
        required_skills=tuple(_string_list(requires.get("skills"))),
        inputs=tuple(_field_dicts(raw.get("inputs"))),
        outputs=tuple(_field_dicts(raw.get("outputs"))),
        scorers=tuple(_string_list(eval_section.get("scorers"))),
        runtime=_string(execution.get("runtime")),
        on_failure=_string(execution.get("on_failure")),
        owner=_string(raw.get("owner")),
        skill_dir=skill_yaml.parent,
        skill_yaml=skill_yaml,
        skill_py=skill_yaml.parent / "skill.py",
    )


def skill_ids_for_family(family: str) -> tuple[str, ...]:
    return FAMILY_SKILLS.get(family, ())


def list_execution_families() -> tuple[str, ...]:
    return tuple(FAMILY_SKILLS)


def import_skill_module(contract: SkillContract, *, prefix: str) -> Any:
    old_path = list(sys.path)
    try:
        for path in (str(repo_root()), str(repo_root() / "src"), str(contract.skill_dir)):
            if path not in sys.path:
                sys.path.insert(0, path)
        package_name = f"{prefix}_{contract.skill_dir.name.replace('.', '_').replace('-', '_')}"
        package = types.ModuleType(package_name)
        package.__path__ = [str(contract.skill_dir)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
        module_name = f"{package_name}.skill"
        spec = importlib.util.spec_from_file_location(module_name, contract.skill_py)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load skill module from {contract.skill_py}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old_path


def skill_import_error(contract: SkillContract) -> str:
    old_modules = dict(sys.modules)
    try:
        import_skill_module(contract, prefix="_brickvision_skill_probe")
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        for name in set(sys.modules) - set(old_modules):
            if name.startswith("_brickvision_skill_probe_"):
                sys.modules.pop(name, None)
    return ""


def runtime_target_exists(target: str) -> bool:
    if target.startswith("file:"):
        path_text, _, attr = target.removeprefix("file:").partition(":")
        path = repo_root() / path_text
        if not path.exists():
            return False
        if not attr:
            return True
        spec = importlib.util.spec_from_file_location("_brickvision_tool_probe", path)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            return False
        finally:
            sys.modules.pop(spec.name, None)
        return callable(getattr(module, attr, None))
    module_name, _, attr = target.partition(":")
    try:
        if importlib.util.find_spec(module_name) is None:
            return False
        if not attr:
            return True
        module = __import__(module_name, fromlist=[attr])
        return callable(getattr(module, attr, None))
    except ModuleNotFoundError:
        return False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _field_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    fields: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        field = {
            "name": _string(item.get("name")),
            "type": _string(item.get("type")),
            "required": bool(item.get("required", False)),
        }
        description = _string(item.get("description"))
        if description:
            field["description"] = description
        if field["name"]:
            fields.append(field)
    return fields


def _category(skill_id: str) -> str:
    if ":" in skill_id and "." in skill_id:
        return skill_id.split(":", 1)[1].split(".", 1)[0]
    return "other"
