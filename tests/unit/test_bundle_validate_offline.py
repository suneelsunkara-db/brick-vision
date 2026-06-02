"""Unit tests for the offline DAB bundle validator (N172).

These tests pin the v0.7.7 capability-graph indexer DAB shape so any
regression to ``databricks.yml`` (missing task, broken DAG edge,
deleted environment, dependency removal, SP mis-wiring) fails fast in
CI without needing a real workspace.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from bundle_validate_offline import (  # noqa: E402
    CAPABILITY_INDEXER_DAG,
    CAPABILITY_INDEXER_TASK_KEYS,
    LAKEBASE_PUBLISH_TASK_KEYS,
    REQUIRED_DEP_PREFIXES,
    VECTOR_SEARCH_TASK_KEYS,
    validate_bundle,
)


# --------------------------------------------------------------------- #
# Live-bundle test — locks down the actual databricks.yml in repo
# --------------------------------------------------------------------- #


def test_live_databricks_yml_validates_offline() -> None:
    """``databricks.yml`` at repo root passes offline DAB validation.

    This is the gate the CI ``bundle-validate`` step runs.
    """
    ok, issues = validate_bundle(REPO_ROOT / "databricks.yml")
    assert ok, f"databricks.yml failed offline validation:\n  - " + "\n  - ".join(issues)


def test_live_capability_indexer_has_expected_task_keys() -> None:
    """Indexer keys match :data:`run_capability_indexer._ALL_TASK_KEYS` exactly."""
    from brickvision_runtime.databricks_jobs.run_capability_indexer import (  # noqa: PLC0415
        _ALL_TASK_KEYS,
    )

    assert set(_ALL_TASK_KEYS) == set(CAPABILITY_INDEXER_TASK_KEYS)
    assert len(_ALL_TASK_KEYS) == 14


def test_indexer_vector_search_uses_runtime_schema_not_legacy_constant() -> None:
    """Vector Search index names must stay under ``BV_SCHEMA``.

    The local deploy provisioner creates ``<BV_CATALOG>.<BV_SCHEMA>.entity_index``.
    The Databricks task wrapper must not drift back to the retired
    ``capability_graph`` schema constant.
    """

    import brickvision_runtime.databricks_jobs.run_capability_indexer as runner  # noqa: PLC0415

    assert not hasattr(runner, "_DEFAULT_SCHEMA")
    assert runner._DEFAULT_VS_INDEX_NAME == "entity_index"


def test_live_capability_indexer_dag_is_canonical() -> None:
    """Indexer DAG matches docs/23-databricks-capability-graph.md §23.3.1."""
    assert CAPABILITY_INDEXER_DAG["plan"] == frozenset()
    assert CAPABILITY_INDEXER_DAG["graph_builder"] == frozenset(
        {"sdk", "openapi_aws", "docs_aws", "labs"},
    )
    assert CAPABILITY_INDEXER_DAG["vs_upsert"] == frozenset({"embed", "persist"})
    assert CAPABILITY_INDEXER_DAG["promote"] == frozenset({"smoke"})
    assert CAPABILITY_INDEXER_DAG["retention"] == frozenset({"promote"})


def test_required_dep_prefix_table_pins_lazy_import_contract() -> None:
    """The required-deps table mirrors the lazy-import contract.

    * ``vector-search`` env -> tasks that import ``databricks.vector_search``
    * ``lakebase-publish`` env -> ``sync`` task; ``databricks-sdk``
      creates/attaches synced tables and ``psycopg`` verifies Postgres
      has caught up to the promoted Delta snapshot.
    """
    assert REQUIRED_DEP_PREFIXES["default"] == ("databricks-sdk",)
    assert REQUIRED_DEP_PREFIXES["vector-search"] == (
        "databricks-sdk",
        "databricks-vectorsearch",
    )
    assert REQUIRED_DEP_PREFIXES["lakebase-publish"] == ("databricks-sdk", "psycopg")
    assert VECTOR_SEARCH_TASK_KEYS == frozenset({"vs_upsert", "smoke"})
    assert LAKEBASE_PUBLISH_TASK_KEYS == frozenset({"sync"})


# --------------------------------------------------------------------- #
# Synthetic-bundle tests — exercise each failure path
# --------------------------------------------------------------------- #


def _block_task(
    task_key: str,
    environment_key: str,
    *,
    depends_on: tuple[str, ...] = (),
) -> str:
    """Render a single task in pure block-style YAML.

    The vendored minyaml loader covers block style fully but only a
    subset of inline-flow style, so we keep test fixtures block-only
    to avoid coupling tests to the loader's flow-style limits.
    """
    lines = [
        f"        - task_key: {task_key}",
        f"          environment_key: {environment_key}",
    ]
    if depends_on:
        lines.append("          depends_on:")
        for dep in depends_on:
            lines.append(f"            - task_key: {dep}")
    return "\n".join(lines)


def _minimal_indexer_yaml(
    *,
    indexer_sp: str = "bv_indexer_sp",
    omit_task_keys: tuple[str, ...] = (),
    override_depends_on: dict[str, tuple[str, ...]] | None = None,
    override_environment_key: dict[str, str] | None = None,
    override_env_dependencies: dict[str, list[str]] | None = None,
) -> str:
    """Build a minimal-but-valid capability-indexer DAB document.

    Hooks let individual tests perturb a single field at a time so we
    can assert that the validator catches each failure mode.
    """
    canonical_depends: dict[str, tuple[str, ...]] = {
        "plan": (),
        "sdk": ("plan",),
        "openapi_aws": ("plan",),
        "docs_aws": ("plan",),
        "labs": ("plan",),
        "graph_builder": ("sdk", "openapi_aws", "docs_aws", "labs"),
        "embed": ("graph_builder",),
        "persist": ("graph_builder",),
        "vs_upsert": ("embed", "persist"),
        "smoke": ("vs_upsert",),
        "promote": ("smoke",),
        "retention": ("promote",),
        "sync": ("promote",),
    }
    canonical_env: dict[str, str] = {
        "plan": "default",
        "sdk": "default",
        "openapi_aws": "default",
        "docs_aws": "default",
        "labs": "default",
        "graph_builder": "default",
        "embed": "default",
        "persist": "default",
        "vs_upsert": "vector-search",
        "smoke": "vector-search",
        "promote": "default",
        "retention": "default",
        "sync": "lakebase-publish",
    }
    if override_depends_on:
        canonical_depends = {**canonical_depends, **override_depends_on}
    if override_environment_key:
        canonical_env = {**canonical_env, **override_environment_key}

    env_deps: dict[str, list[str]] = {
        "default": ["databricks-sdk>=0.68"],
        "vector-search": ["databricks-sdk>=0.68", "databricks-vectorsearch>=0.40"],
        "lakebase-publish": ["databricks-sdk>=0.68", "psycopg[binary]>=3.2,<4.0"],
    }
    if override_env_dependencies:
        env_deps = {**env_deps, **override_env_dependencies}

    def _render_deps(items: list[str]) -> str:
        return "[" + ", ".join(f'"{d}"' for d in items) + "]"

    task_blocks = [
        _block_task(
            task_key=key,
            environment_key=canonical_env[key],
            depends_on=canonical_depends[key],
        )
        for key in CAPABILITY_INDEXER_TASK_KEYS
        if key not in omit_task_keys
    ]
    tasks_yaml = "\n".join(task_blocks)
    return textwrap.dedent(f"""\
    bundle:
      name: brickvision

    variables:
      catalog:
        default: brickvision_dev
      serverless_env_version:
        default: "2"
      indexer_sp:
        default: {indexer_sp}

    resources:
      jobs:
        capability_indexer:
          name: bv_capability_indexer
          run_as:
            service_principal_name: ${{var.indexer_sp}}
          environments:
            - environment_key: default
              spec:
                client: ${{var.serverless_env_version}}
                dependencies: {_render_deps(env_deps['default'])}
            - environment_key: vector-search
              spec:
                client: ${{var.serverless_env_version}}
                dependencies: {_render_deps(env_deps['vector-search'])}
            - environment_key: lakebase-publish
              spec:
                client: ${{var.serverless_env_version}}
                dependencies: {_render_deps(env_deps['lakebase-publish'])}
          tasks:
    """) + tasks_yaml + "\n"


_MINIMAL_INDEXER = _minimal_indexer_yaml()


def test_minimal_clean_bundle_validates(tmp_path: Path) -> None:
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(_MINIMAL_INDEXER)
    ok, issues = validate_bundle(bundle)
    assert ok, issues


def test_missing_capability_indexer_fails(tmp_path: Path) -> None:
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(textwrap.dedent("""\
    bundle:
      name: brickvision
    variables:
      catalog:
        default: brickvision_dev
    resources:
      jobs:
        hitl_watcher:
          name: brickvision_hitl_watcher
          environments:
            - environment_key: default
              spec:
                client: "2"
                dependencies: ["databricks-sdk>=0.68"]
          tasks:
            - task_key: run
              environment_key: default
    """))
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any("capability_indexer is missing" in i for i in issues)


def test_dropped_task_is_flagged(tmp_path: Path) -> None:
    """Removing a task from the indexer is caught."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(_minimal_indexer_yaml(omit_task_keys=("retention",)))
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any("missing tasks" in i and "retention" in i for i in issues)


def test_broken_dag_edge_is_flagged(tmp_path: Path) -> None:
    """Wiring retention -> vs_upsert (skipping promote) is caught."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(
        _minimal_indexer_yaml(
            override_depends_on={"retention": ("vs_upsert",)},
        ),
    )
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any(
        "task 'retention' depends_on" in i and "promote" in i
        for i in issues
    )


def test_wrong_environment_for_vs_task_is_flagged(tmp_path: Path) -> None:
    """Putting vs_upsert on the default env (no databricks-vectorsearch) fails."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(
        _minimal_indexer_yaml(
            override_environment_key={"vs_upsert": "default"},
        ),
    )
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any(
        "vector-search environment must be used" in i for i in issues
    )


def test_missing_dependency_is_flagged(tmp_path: Path) -> None:
    """Dropping databricks-vectorsearch from the vector-search env is caught."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(
        _minimal_indexer_yaml(
            override_env_dependencies={"vector-search": ["databricks-sdk>=0.68"]},
        ),
    )
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any(
        "environment 'vector-search' missing required dependency" in i
        and "databricks-vectorsearch" in i
        for i in issues
    )


def test_wrong_service_principal_is_flagged(tmp_path: Path) -> None:
    """Pointing run_as at bv_app_sp (the app SP) violates SP isolation."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(_minimal_indexer_yaml(indexer_sp="bv_app_sp"))
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any(
        "service_principal_name must resolve to 'bv_indexer_sp'" in i
        for i in issues
    )


def test_undeclared_environment_key_is_flagged(tmp_path: Path) -> None:
    """Referencing an env key that wasn't declared on the Job fails."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(
        _minimal_indexer_yaml(
            override_environment_key={"plan": "not-declared"},
        ),
    )
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any("undefined environment_key 'not-declared'" in i for i in issues)


def test_unknown_depends_on_target_is_flagged(tmp_path: Path) -> None:
    """depends_on referencing a non-existent task is caught."""
    bundle = tmp_path / "databricks.yml"
    bundle.write_text(
        _minimal_indexer_yaml(
            override_depends_on={"promote": ("nonexistent",)},
        ),
    )
    ok, issues = validate_bundle(bundle)
    assert not ok
    assert any("depends_on unknown task" in i for i in issues)
