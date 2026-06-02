"""Offline structural validator for ``databricks.yml``.

The Databricks CLI's ``databricks bundle validate`` command always
makes a SCIM ``/Me`` round-trip to the workspace, which fails in CI
environments without a live workspace. This script reproduces the
parts of ``validate`` that DON'T need a workspace, plus the
v0.7.7-specific DAG invariants that the generic CLI cannot know
about.

What this validator checks
==========================

Generic (every Job in ``resources.jobs.*``):

* ``task_key`` is unique within a Job.
* Every task carries an ``environment_key`` referencing one of the
  Job's job-level ``environments[*].environment_key``.
* Every ``depends_on[*].task_key`` references a real ``task_key``.
* The Job's task graph is acyclic.
* Each environment's ``spec.client`` is the supported serverless
  version ``"2"`` and ``spec.dependencies`` includes
  ``databricks-sdk``.

Capability-graph-specific (``resources.jobs.capability_indexer``):

* Exactly the 14 task keys from
  :data:`brickvision_runtime.databricks_jobs.run_capability_indexer
  ._ALL_TASK_KEYS` are present.
* The DAG matches the canonical shape from
  ``docs/23-databricks-capability-graph.md §23.3.1``:

      plan
        -> sdk, openapi, docs, blog, labs    (5 sources, parallel)
      sdk, openapi, docs, blog, labs
        -> graph_builder                      (fan-in)
      graph_builder
        -> embed, persist                     (parallel)
      embed, persist
        -> vs_upsert
      vs_upsert
        -> smoke
      smoke
        -> promote
      promote
        -> retention, sync                    (parallel)

  ``sync`` is the v0.7.7 Lakebase-Autoscaling Synced-Tables sync
  task; it runs in parallel with ``retention`` after a successful
  promote and is the end-to-end gate that waits until Lakebase synced
  tables expose the promoted Delta snapshot.

* The Job's ``run_as.service_principal_name`` is the
  ``${var.indexer_sp}`` template (enforces SP isolation per
  directive 1 / §23.3.6).
* The ``vector-search`` environment is referenced exactly by
  ``vs_upsert`` and ``smoke``; the ``lakebase-publish`` environment
  is referenced exactly by ``sync``; every other task uses
  ``default``.
* ``vector-search`` includes ``databricks-vectorsearch`` per the
  lazy-import contract in :mod:`brickvision_runtime.capability_graph
  .smoke` + :mod:`.vs_upsert`. ``lakebase-publish`` includes
  ``databricks-sdk`` for the Synced Tables API and ``psycopg`` so the
  sync task can poll Lakebase Postgres until synced tables expose the
  promoted snapshot.

Usage
=====

::

    python3 scripts/bundle_validate_offline.py            # validates databricks.yml
    python3 scripts/bundle_validate_offline.py path.yml   # validates a custom path

Exits 0 on success, 1 with a diff-style report on failure.

This is the script the CI ``bundle-validate`` step actually runs.
The ``databricks bundle validate`` command is then a *non-blocking*
follow-up that workspace-side CI runs against a real ephemeral
workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Prefer PyYAML when available (production CI venv); otherwise fall back
# to the vendored minimal loader so this script runs even on a bare
# Python interpreter. Both load the small subset of YAML used by
# ``databricks.yml``.
try:
    import yaml as _yaml  # type: ignore[import-not-found]

    def _safe_load(text: str) -> Any:  # noqa: ANN401
        return _yaml.safe_load(text)

except ImportError:  # pragma: no cover — vendored fallback
    _REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(_REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT / "src"))
    from brickvision_runtime._vendor.minyaml import safe_load as _safe_load  # type: ignore[no-redef]

# --------------------------------------------------------------------- #
# Constants pinned by docs/23-databricks-capability-graph.md §23.3.1
# --------------------------------------------------------------------- #

CAPABILITY_INDEXER_TASK_KEYS: tuple[str, ...] = (
    "plan",
    "sdk",
    "openapi_aws",
    "docs_aws",
    "labs",
    "graph_builder",
    "embed",
    "persist",
    "vs_upsert",
    "smoke",
    "promote",
    "retention",
    "sync",
)

CAPABILITY_INDEXER_DAG: dict[str, frozenset[str]] = {
    "plan": frozenset(),
    "sdk": frozenset({"plan"}),
    "openapi_aws": frozenset({"plan"}),
    "docs_aws": frozenset({"plan"}),
    "labs": frozenset({"plan"}),
    "graph_builder": frozenset({"sdk", "openapi_aws", "docs_aws", "labs"}),
    "embed": frozenset({"graph_builder"}),
    "persist": frozenset({"graph_builder"}),
    "vs_upsert": frozenset({"embed", "persist"}),
    "smoke": frozenset({"vs_upsert"}),
    "promote": frozenset({"smoke"}),
    "retention": frozenset({"promote"}),
    "sync": frozenset({"promote"}),
}

VECTOR_SEARCH_TASK_KEYS: frozenset[str] = frozenset({"vs_upsert", "smoke"})
LAKEBASE_PUBLISH_TASK_KEYS: frozenset[str] = frozenset({"sync"})

REQUIRED_ENV_BY_TASK: dict[str, tuple[str, ...]] = {
    "vs_upsert": ("BV_INDEXER_VS_INDEX_NAME={{job.parameters.vs_index_name}}",),
    "smoke": ("BV_INDEXER_VS_INDEX_NAME={{job.parameters.vs_index_name}}",),
}

REQUIRED_DEP_PREFIXES: dict[str, tuple[str, ...]] = {
    "default": ("databricks-sdk",),
    "vector-search": ("databricks-sdk", "databricks-vectorsearch"),
    "lakebase-publish": ("databricks-sdk", "psycopg"),
}

_EXPECTED_INDEXER_PYTHON_FILE = (
    "src/brickvision_runtime/databricks_jobs/run_capability_indexer.py"
)


# --------------------------------------------------------------------- #
# ${var.*} substitution
# --------------------------------------------------------------------- #


_VAR_TOKEN_PATTERN = "${var."


def _collect_var_defaults(bundle: dict[str, Any]) -> dict[str, str]:
    """Collect variable defaults from the bundle's ``variables`` block."""
    defaults: dict[str, str] = {}
    for name, spec in (bundle.get("variables") or {}).items():
        if isinstance(spec, dict) and "default" in spec:
            defaults[name] = str(spec["default"])
    return defaults


def _substitute(value: Any, defaults: dict[str, str]) -> Any:  # noqa: ANN401
    """Recursively replace ``${var.X}`` tokens in any string leaves."""
    if isinstance(value, str):
        if _VAR_TOKEN_PATTERN not in value:
            return value
        result = value
        for var_name, var_value in defaults.items():
            result = result.replace(f"${{var.{var_name}}}", var_value)
        return result
    if isinstance(value, list):
        return [_substitute(item, defaults) for item in value]
    if isinstance(value, dict):
        return {k: _substitute(v, defaults) for k, v in value.items()}
    return value


# --------------------------------------------------------------------- #
# Issue collector
# --------------------------------------------------------------------- #


class IssueCollector:
    """Accumulates validation errors so we can surface them all at once."""

    def __init__(self) -> None:
        self._issues: list[str] = []

    def err(self, msg: str) -> None:
        self._issues.append(msg)

    @property
    def issues(self) -> list[str]:
        return list(self._issues)

    @property
    def ok(self) -> bool:
        return not self._issues


# --------------------------------------------------------------------- #
# Generic Job-level checks
# --------------------------------------------------------------------- #


def _validate_job(
    name: str,
    job: dict[str, Any],
    collector: IssueCollector,
) -> None:
    envs = {
        env.get("environment_key"): env
        for env in (job.get("environments") or [])
        if isinstance(env, dict)
    }
    if not envs:
        collector.err(f"job '{name}': no environments declared")
    for env_key, env in envs.items():
        spec = env.get("spec") or {}
        if str(spec.get("client", "")) != "2":
            collector.err(
                f"job '{name}': environment '{env_key}' has "
                f"client={spec.get('client')!r}; required: '2'",
            )
        deps = list(spec.get("dependencies") or [])
        if not any(d.startswith("databricks-sdk") for d in deps):
            collector.err(
                f"job '{name}': environment '{env_key}' missing "
                "databricks-sdk dependency",
            )

    tasks = job.get("tasks") or []
    seen_keys: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            collector.err(f"job '{name}': non-mapping task entry")
            continue
        key = task.get("task_key")
        if not key:
            collector.err(f"job '{name}': task missing task_key")
            continue
        if key in seen_keys:
            collector.err(f"job '{name}': duplicate task_key '{key}'")
        seen_keys.add(key)
        env_key = task.get("environment_key")
        if not env_key:
            collector.err(
                f"job '{name}': task '{key}' missing environment_key",
            )
        elif env_key not in envs:
            collector.err(
                f"job '{name}': task '{key}' references undefined "
                f"environment_key '{env_key}'",
            )
        for dep in task.get("depends_on") or []:
            dep_key = (dep or {}).get("task_key")
            if not dep_key:
                collector.err(
                    f"job '{name}': task '{key}' has empty depends_on entry",
                )

    for task in tasks:
        if not isinstance(task, dict):
            continue
        key = task.get("task_key")
        if not key:
            continue
        for dep in task.get("depends_on") or []:
            dep_key = (dep or {}).get("task_key")
            if dep_key and dep_key not in seen_keys:
                collector.err(
                    f"job '{name}': task '{key}' depends_on unknown "
                    f"task '{dep_key}'",
                )

    _check_acyclic(name, tasks, collector)


def _check_acyclic(
    name: str,
    tasks: list[dict[str, Any]],
    collector: IssueCollector,
) -> None:
    edges: dict[str, list[str]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        key = task.get("task_key")
        if not key:
            continue
        edges[key] = [
            (dep or {}).get("task_key", "")
            for dep in (task.get("depends_on") or [])
        ]
    color: dict[str, int] = {k: 0 for k in edges}  # 0 = white, 1 = grey, 2 = black

    def dfs(node: str) -> bool:
        if color.get(node) == 1:
            return True
        if color.get(node) == 2:
            return False
        color[node] = 1
        for neighbor in edges.get(node, []):
            if neighbor and dfs(neighbor):
                return True
        color[node] = 2
        return False

    for node in list(edges):
        if dfs(node):
            collector.err(f"job '{name}': cycle detected in task DAG")
            return


# --------------------------------------------------------------------- #
# Capability-indexer-specific checks (docs/23 §23.3.1)
# --------------------------------------------------------------------- #


def _validate_capability_indexer(
    job: dict[str, Any] | None,
    collector: IssueCollector,
) -> None:
    if job is None:
        collector.err(
            "resources.jobs.capability_indexer is missing; "
            "v0.7.7 capability-graph indexer requires this Job",
        )
        return

    sp = (job.get("run_as") or {}).get("service_principal_name")
    if sp != "bv_indexer_sp":
        collector.err(
            "capability_indexer: run_as.service_principal_name must "
            "resolve to 'bv_indexer_sp' (via ${var.indexer_sp}); "
            f"got {sp!r}",
        )

    tasks = {
        task["task_key"]: task
        for task in (job.get("tasks") or [])
        if isinstance(task, dict) and task.get("task_key")
    }

    expected = set(CAPABILITY_INDEXER_TASK_KEYS)
    actual = set(tasks)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        collector.err(
            f"capability_indexer: missing tasks {missing!r}",
        )
    if extra:
        collector.err(
            f"capability_indexer: unexpected tasks {extra!r}",
        )

    for task_key, expected_deps in CAPABILITY_INDEXER_DAG.items():
        task = tasks.get(task_key)
        if task is None:
            continue
        actual_deps = frozenset(
            (dep or {}).get("task_key", "")
            for dep in (task.get("depends_on") or [])
        )
        if actual_deps != expected_deps:
            collector.err(
                f"capability_indexer: task '{task_key}' depends_on "
                f"{sorted(actual_deps)!r}; expected {sorted(expected_deps)!r}",
            )

    by_env: dict[str, set[str]] = {}
    for task_key, task in tasks.items():
        env_key = task.get("environment_key")
        if env_key:
            by_env.setdefault(env_key, set()).add(task_key)

    if by_env.get("vector-search", set()) != VECTOR_SEARCH_TASK_KEYS:
        collector.err(
            "capability_indexer: vector-search environment must be used "
            f"by exactly {sorted(VECTOR_SEARCH_TASK_KEYS)!r}; got "
            f"{sorted(by_env.get('vector-search', set()))!r}",
        )
    if by_env.get("lakebase-publish", set()) != LAKEBASE_PUBLISH_TASK_KEYS:
        collector.err(
            "capability_indexer: lakebase-publish environment must be "
            f"used by exactly {sorted(LAKEBASE_PUBLISH_TASK_KEYS)!r}; got "
            f"{sorted(by_env.get('lakebase-publish', set()))!r}",
        )

    envs = {
        env.get("environment_key"): env
        for env in (job.get("environments") or [])
        if isinstance(env, dict)
    }
    for env_key, prefixes in REQUIRED_DEP_PREFIXES.items():
        env = envs.get(env_key)
        if env is None:
            collector.err(
                f"capability_indexer: missing required environment '{env_key}'",
            )
            continue
        deps = list((env.get("spec") or {}).get("dependencies") or [])
        for prefix in prefixes:
            if not any(d.startswith(prefix) for d in deps):
                collector.err(
                    f"capability_indexer: environment '{env_key}' missing "
                    f"required dependency starting with '{prefix}'",
                )

    for task_key, task in tasks.items():
        spark_task = task.get("spark_python_task") or {}
        if not spark_task:
            continue
        python_file = spark_task.get("python_file")
        if python_file != _EXPECTED_INDEXER_PYTHON_FILE:
            collector.err(
                f"capability_indexer: task '{task_key}' python_file={python_file!r}; "
                f"expected {_EXPECTED_INDEXER_PYTHON_FILE!r}",
            )
        params = list(spark_task.get("parameters") or [])
        for required_env in REQUIRED_ENV_BY_TASK.get(task_key, ()):
            if required_env not in params:
                collector.err(
                    f"capability_indexer: task '{task_key}' missing "
                    f"--env {required_env!r}",
                )


# --------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------- #


def validate_bundle(path: Path) -> tuple[bool, list[str]]:
    """Validate a Databricks Asset Bundle YAML file.

    Returns ``(ok, issues)``. ``ok`` is True iff ``issues`` is empty.
    """
    text = path.read_text(encoding="utf-8")
    raw_bundle = _safe_load(text) or {}
    if not isinstance(raw_bundle, dict):
        return False, [f"bundle root is not a mapping at {path}"]

    defaults = _collect_var_defaults(raw_bundle)
    bundle = _substitute(raw_bundle, defaults)

    collector = IssueCollector()

    if "bundle" not in bundle:
        collector.err("missing top-level 'bundle' block")
    if "resources" not in bundle:
        collector.err("missing top-level 'resources' block")

    jobs = ((bundle.get("resources") or {}).get("jobs") or {})
    if not isinstance(jobs, dict):
        collector.err("resources.jobs must be a mapping")
        return collector.ok, collector.issues

    for name, job in jobs.items():
        if not isinstance(job, dict):
            collector.err(f"resources.jobs.{name} must be a mapping")
            continue
        _validate_job(name, job, collector)

    _validate_capability_indexer(jobs.get("capability_indexer"), collector)

    return collector.ok, collector.issues


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("databricks.yml")
    if not target.exists():
        sys.stderr.write(f"error: bundle file not found: {target}\n")
        return 2
    ok, issues = validate_bundle(target)
    if ok:
        sys.stdout.write(
            f"OK: {target} passes offline DAB structural validation "
            "(14-task capability_indexer DAG + per-Job environment_key + "
            "deps coverage).\n",
        )
        return 0
    sys.stderr.write(f"FAIL: {target} failed offline DAB validation:\n")
    for issue in issues:
        sys.stderr.write(f"  - {issue}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
