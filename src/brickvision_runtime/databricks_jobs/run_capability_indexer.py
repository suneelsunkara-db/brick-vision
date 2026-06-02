"""Databricks Job entry-point for the v0.7.7 Capability Graph indexer
(per docs/23-databricks-capability-graph.md §23.3.1).

This is the **integration layer** for the 11 library modules in
:mod:`brickvision_runtime.capability_graph`. It contains no business
logic of its own — every algorithmic decision lives in the library
modules; this file's job is to:

  1. Lazily construct the production wrappers for each external
     dependency (FMS Foundation Model Serving, Spark+Delta,
     Mosaic AI Vector Search, the Databricks SDK).
  2. Read the upstream task's output from a UC Volume staging path
     (the multi-task Job's inter-task communication channel).
  3. Invoke the corresponding library function.
  4. Serialize the result back to the staging path for the next task.
  5. Emit task-level telemetry + return the appropriate exit code.

Why a single file with --task sub-commands instead of 14 files
==============================================================

Per §23.3.1, the indexer is a **14-task multi-task serverless Job**
(13 core tasks + ``sync`` for Lakebase Autoscaling Synced Tables);
each task is its own JVM/process invocation declared in the DAB
(``databricks.yml``). The DAB invokes THIS file 14 times with
different ``--task`` arguments. Reasons for the single-file pattern:

  * **Shared helpers** — ``_read_env``, ``_staging_path``,
    ``_emit_result_json``, etc. are written once and reused by
    every task. 13 files would duplicate these.
  * **Audit trail** — reviewing "what does the indexer do?" requires
    reading exactly one Python file. The dispatch happens at
    ``main()``; everything else is callee logic.
  * **DAB simplicity** — every task points at the SAME
    ``python_file``; only the ``parameters: ["--task", "<key>", ...]``
    differs. Adding a new task is a one-line DAB change plus a
    new ``run_<task>`` function in this file.

Env-var propagation via Job parameters
======================================

Serverless task environments (DAB ``environments[*].spec``) only
support ``client`` / ``dependencies`` / ``environment_version`` —
NOT ``environment_variables``. Per the documented canonical pattern
(https://docs.databricks.com/aws/en/jobs/task-parameters):

  > Job parameters are not automatically pushed down to tasks that
  > use JSON arrays. You can reference job parameters in the task
  > JSON-formatted array using the dynamic value reference
  > ``{{job.parameters.<name>}}``.

So the DAB declares each ``BV_*`` env var as a job-level parameter
templated from a DAB variable (``${var.indexer_warehouse_id}``,
etc.), and each task's ``parameters: [...]`` array passes them
through as ``--env BV_KEY={{job.parameters.bv_key}}``. The
dispatcher's ``--env`` flag is repeatable; each ``KEY=VALUE`` is
applied to ``os.environ`` before the task function runs. This is
how ``BV_INDEXER_WAREHOUSE_ID``, ``BV_LAKEBASE_PROJECT_ID``, etc.
flow into the running task without per-task ``environment_variables``
support in DAB.

Inter-task data flow
====================

Each task writes its output to::

    /Volumes/<catalog>/<schema>/<state_volume>/runs/<run_id>/<task_key>.json

where ``<run_id>`` is the Job's ``{{job.run_id}}``, ``<task_key>``
matches the DAB's ``task_key`` field, and ``<state_volume>`` is the
UC Volume name resolved from ``BV_INDEXER_STATE_VOLUME`` (default
``indexer-state``). Downstream tasks read upstream JSONs by name.
This is the Databricks-standard inter-task communication channel
for multi-task Jobs (since processes don't share memory and Job
parameters can't carry megabytes of data).

The Volume is **NOT** a state store — every typed row of capability-
graph state lives in Delta tables; the Volume holds only short-lived
per-run JSON hand-offs that the retention task GCs.

The state volume is auto-created by the install pre-flight
:func:`brickvision.install.preflight.capability_graph.
check_uc_schema_capability_graph_ownership`; if missing, the
``plan`` task fails fast with a useful error.

Lazy-loading discipline
=======================

This module imports the following ONLY inside task functions:

  * ``databricks.sdk`` (workspace client construction)
  * ``pyspark.*`` (Spark session, DataFrames)
  * ``databricks.vector_search.*`` (VS client)

So this file imports cleanly on a development machine WITHOUT those
dependencies installed; the dev-machine smoke tests (which mock the
protocol seams in the library modules) work fine.

Error handling
==============

Each task wraps its core call in try/except and:

  * On success: writes ``<task_key>.json`` to staging, exits 0.
  * On exception: writes ``<task_key>.error.json`` to staging with
    the failure ReasonCode + traceback, exits non-zero. The DAB's
    task-level retry policy retries up to 2 times with
    ``min_retry_interval_millis: 60_000`` (1 minute). After 2
    failures, the DAB's ``email_notifications`` + Slack webhook
    fire (per §23.3.9).

Reason codes
============

Per §23.3.1, each task surfaces a specific ReasonCode on failure;
all 28 are already in :class:`brickvision_runtime.failures.
ReasonCode`. The mapping is task → primary failure code:

==============   =================================================
Task             Primary failure code
==============   =================================================
plan             CAPABILITY_GRAPH_PLAN_FAILED
sdk              CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (sdk)
openapi_aws      CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (openapi_aws)
openapi_azure    CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (openapi_azure)
openapi_gcp      CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (openapi_gcp)
docs_aws         CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (docs_aws)
docs_azure       CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (docs_azure)
docs_gcp         CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (docs_gcp)
blog             CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (blog)
labs             CAPABILITY_GRAPH_SOURCE_PARSE_FAILED (labs)
graph_builder    CAPABILITY_GRAPH_BUILD_FAILED
embed            CAPABILITY_GRAPH_EMBEDDING_*_EXCEEDED / ENDPOINT_ERROR
persist          CAPABILITY_GRAPH_PERSIST_WRITE_FAILED
vs_upsert        CAPABILITY_GRAPH_VS_UPSERT_FAILED
smoke            CAPABILITY_GRAPH_SMOKE_FAILED / SMOKE_BASELINE_EMPTY
promote          CAPABILITY_GRAPH_PROMOTE_GATE_FAILED / WRITE_FAILED
retention        CAPABILITY_GRAPH_RETENTION_*_FAILED
sync             CAPABILITY_GRAPH_PUBLISH_FAILED
==============   =================================================
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
import traceback
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


# When Databricks runs this as a spark_python_task, __file__ is not defined
# (the script is exec'd). Derive src root for sibling package imports.
try:
    _SRC_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
    # In Databricks exec context, co_filename holds the workspace path
    # (set by compile(source, filename, 'exec') in the driver wrapper).
    import inspect as _inspect  # noqa: PLC0415
    _frame = _inspect.currentframe()
    _co = _frame.f_code.co_filename if _frame else ""
    del _inspect, _frame
    if _co and "/brickvision_runtime/" in _co:
        _SRC_ROOT = str(Path(_co).resolve().parents[2])
    else:
        _SRC_ROOT = ""

if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)


# ---------------------------------------------------------------------------
# Constants — matched against ``BV_INDEXER_*`` env vars per §23.3.6
# ---------------------------------------------------------------------------


_DEFAULT_CATALOG: str = "brickvision"
_DEFAULT_VS_ENDPOINT: str = "brickvision-dev"
_DEFAULT_EMBEDDING_ENDPOINT: str = "databricks-qwen3-embedding-0-6b"
_DEFAULT_EMBEDDING_DIM: int = 1024
_DEFAULT_VS_INDEX_NAME: str = "entity_index"

_TASK_KEY_PLAN: str = "plan"
_TASK_KEY_SDK: str = "sdk"
_TASK_KEY_OPENAPI_AWS: str = "openapi_aws"
_TASK_KEY_OPENAPI_AZURE: str = "openapi_azure"
_TASK_KEY_OPENAPI_GCP: str = "openapi_gcp"
_TASK_KEY_DOCS_AWS: str = "docs_aws"
_TASK_KEY_DOCS_AZURE: str = "docs_azure"
_TASK_KEY_DOCS_GCP: str = "docs_gcp"
_TASK_KEY_BLOG: str = "blog"
_TASK_KEY_LABS: str = "labs"
_TASK_KEY_GRAPH_BUILDER: str = "graph_builder"
_TASK_KEY_EMBED: str = "embed"
_TASK_KEY_PERSIST: str = "persist"
_TASK_KEY_VS_UPSERT: str = "vs_upsert"
_TASK_KEY_SMOKE: str = "smoke"
_TASK_KEY_PROMOTE: str = "promote"
_TASK_KEY_RETENTION: str = "retention"
_TASK_KEY_SYNC: str = "sync"

_ALL_TASK_KEYS: tuple[str, ...] = (
    _TASK_KEY_PLAN,
    _TASK_KEY_SDK,
    _TASK_KEY_OPENAPI_AWS,
    _TASK_KEY_OPENAPI_AZURE,
    _TASK_KEY_OPENAPI_GCP,
    _TASK_KEY_DOCS_AWS,
    _TASK_KEY_DOCS_AZURE,
    _TASK_KEY_DOCS_GCP,
    _TASK_KEY_BLOG,
    _TASK_KEY_LABS,
    _TASK_KEY_GRAPH_BUILDER,
    _TASK_KEY_EMBED,
    _TASK_KEY_PERSIST,
    _TASK_KEY_VS_UPSERT,
    _TASK_KEY_SMOKE,
    _TASK_KEY_PROMOTE,
    _TASK_KEY_RETENTION,
    _TASK_KEY_SYNC,
)


# ---------------------------------------------------------------------------
# Helpers — env, staging paths, JSON I/O, telemetry
# ---------------------------------------------------------------------------


def _read_env(name: str, *, default: str | None = None) -> str:
    """Read a required environment variable.

    Raises :class:`RuntimeError` with a code-aligned message when the
    variable is missing AND no ``default`` is provided. Trims
    whitespace; treats empty strings as missing.
    """

    value = os.environ.get(name, "").strip()
    if value:
        return value
    if default is not None:
        return default
    raise RuntimeError(f"required env var {name} is missing or empty")


def _read_env_int(name: str, *, default: int) -> int:
    """Read an integer env var with a default."""

    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"env var {name}={raw!r} could not be parsed as int"
        ) from exc


def _read_env_float(name: str, *, default: float) -> float:
    """Read a float env var with a default."""

    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"env var {name}={raw!r} could not be parsed as float"
        ) from exc


def _now_ms() -> int:
    """Wall-clock in milliseconds. Centralized so tests can override."""

    return int(time.time() * 1000)


def _clone_repo(url: str, *, shallow: bool = True, branch: str | None = None) -> str:
    """Clone a git repo into a temp directory and return the path."""

    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    dest = tempfile.mkdtemp(prefix="bv_src_")
    cmd = ["git", "clone"]
    if shallow:
        cmd += ["--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, dest]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    return dest


def _fetch_sitemap_urls(sitemap_url: str) -> list[str]:
    """Recursively fetch all URLs from a sitemap (handles sitemap indexes)."""

    import urllib.request  # noqa: PLC0415
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    try:
        req = urllib.request.Request(sitemap_url, headers={"User-Agent": "BrickVision-Indexer/0.7.7"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_bytes = resp.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    # Check if this is a sitemap index (contains <sitemap> elements)
    sub_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
    if sub_sitemaps:
        urls: list[str] = []
        for loc in sub_sitemaps:
            if loc.text:
                urls.extend(_fetch_sitemap_urls(loc.text))
        return urls

    # Regular sitemap — extract <url><loc> entries
    return [loc.text for loc in root.findall(".//sm:loc", ns) if loc.text]


def _fetch_pages_batch(urls: list[str], *, delay_sec: float = 0.25) -> list[tuple[str, str]]:
    """Fetch HTML for a batch of URLs with rate limiting. Returns (url, html) pairs."""

    import urllib.request  # noqa: PLC0415

    results: list[tuple[str, str]] = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BrickVision-Indexer/0.7.7"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            results.append((url, html))
        except Exception:
            continue
        if delay_sec > 0:
            time.sleep(delay_sec)
    return results


_DOCS_SITEMAPS: dict[str, str] = {
    "aws": "https://docs.databricks.com/aws/en/sitemap.xml",
    "azure": "https://docs.databricks.com/azure/en/sitemap.xml",
    "gcp": "https://docs.databricks.com/gcp/en/sitemap.xml",
}


def _fetch_docs_pages_for_cloud(cloud: str, *, max_pages: int = 0) -> list[Any]:
    """Fetch docs pages from a single cloud's Databricks docs sitemap."""

    from brickvision_runtime.capability_graph.sources import docs_adapter  # noqa: PLC0415

    sitemap_url = _DOCS_SITEMAPS.get(cloud, "")
    if not sitemap_url:
        return []

    all_urls = _fetch_sitemap_urls(sitemap_url)

    skip_patterns = ("/release-notes/", "/error-messages/", "/archive/", "/index.html")
    all_urls = [u for u in all_urls if not any(p in u for p in skip_patterns)]

    if max_pages > 0:
        all_urls = all_urls[:max_pages]

    fetched = _fetch_pages_batch(all_urls, delay_sec=0.05)
    fetched_at = _now_ms()

    return [
        docs_adapter.DocsPageInput(url=url, raw_html=html, fetched_at_ms=fetched_at)
        for url, html in fetched
    ]


def _fetch_blog_posts(*, max_posts: int = 0) -> list[Any]:
    """Fetch blog posts from databricks.com/blog sitemap."""

    from brickvision_runtime.capability_graph.sources import blog_adapter  # noqa: PLC0415

    all_urls = _fetch_sitemap_urls("https://www.databricks.com/en-blog-assets/sitemap/sitemap-index.xml")

    # Filter out non-skill categories
    skip_categories = ("/customer-stories/", "/events/", "/news/", "/company-blog/",
                       "/press-releases/", "/careers/", "/legal/")
    all_urls = [u for u in all_urls if not any(c in u for c in skip_categories)]

    if max_posts > 0:
        all_urls = all_urls[:max_posts]

    fetched = _fetch_pages_batch(all_urls, delay_sec=0.05)
    fetched_at = _now_ms()

    return [
        blog_adapter.BlogPostInput(url=url, raw_html=html, fetched_at_ms=fetched_at)
        for url, html in fetched
    ]


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


def _resolve_state_volume() -> str:
    """Return the indexer-state UC Volume name (per ``BV_INDEXER_STATE_VOLUME``).

    The Volume is the inter-task JSON hand-off channel for the 13
    indexer tasks; default ``"indexer-state"`` (renamed from ``"indexer"``
    in v0.7.7 to make explicit that it is NOT a state store — every
    typed capability-graph row lives in Delta).
    """

    return os.environ.get("BV_INDEXER_STATE_VOLUME", "indexer-state").strip() or "indexer-state"


def _staging_root(*, catalog: str, run_id: str) -> str:
    """Return the indexer-state Volume directory for this Job run.

    Per §23.3.1 the path is::

        /Volumes/<catalog>/<schema>/<state_volume>/runs/<run_id>/

    where ``<state_volume>`` resolves from ``BV_INDEXER_STATE_VOLUME``
    (default ``indexer-state``). A run_id of ``"local"`` is used by
    offline smoke tests so the code path is exercised without writing
    into a real UC Volume.
    """

    return (
        f"/Volumes/{catalog}/{_resolve_schema()}/{_resolve_state_volume()}/runs/{run_id}"
    )


def _staging_path(*, catalog: str, run_id: str, task_key: str) -> str:
    """Return the success-artifact path for a task."""

    return f"{_staging_root(catalog=catalog, run_id=run_id)}/{task_key}.json"


def _staging_error_path(*, catalog: str, run_id: str, task_key: str) -> str:
    """Return the error-artifact path for a task."""

    return f"{_staging_root(catalog=catalog, run_id=run_id)}/{task_key}.error.json"


def _write_json_artifact(*, path: str, payload: Mapping[str, Any]) -> None:
    """Write a UTF-8 JSON file atomically.

    Production: writes to UC Volume via stdlib ``open()`` (UC Volumes
    appear as a POSIX-mounted FS on Databricks Runtime; a write-then-
    rename pattern is unnecessary because the runtime guarantees
    visibility on close).
    """

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def _read_json_artifact(*, path: str) -> Mapping[str, Any]:
    """Read a JSON artifact written by an upstream task."""

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"artifact at {path!r} is not a JSON object")
    return data


def _hydrate(cls: type, data: Any) -> Any:
    """Typed hydration: rebuild a frozen dataclass from a JSON-loaded dict.

    Recursively handles ``tuple[X, ...]`` (coerced from JSON list),
    nested dataclass fields, ``Mapping`` types (returned as-is), and
    ``X | None`` unions. Used by every downstream task to reconstruct
    the typed library objects that ``json.load`` returned as plain
    dicts/lists.

    The reason this exists: the library functions are strongly typed
    (``frozen=True`` dataclasses, ``tuple[X, ...]`` instead of
    ``list[X]``); JSON round-trips lose both the class identity and
    the tuple-vs-list distinction. Without this helper, every
    downstream task would have ~30 lines of manual coercion per
    library call.
    """

    if data is None:
        return None
    if not (isinstance(data, dict) and dataclasses.is_dataclass(cls)):
        return data

    type_hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name in data:
            kwargs[field.name] = _coerce(
                type_hints.get(field.name, field.type), data[field.name],
            )
        elif field.default is not dataclasses.MISSING:
            kwargs[field.name] = field.default
        elif field.default_factory is not dataclasses.MISSING:
            kwargs[field.name] = field.default_factory()
        else:
            # Supply a zero-value for required fields missing from the JSON
            kwargs[field.name] = _zero_value(type_hints.get(field.name, field.type))
    return cls(**kwargs)


def _zero_value(type_hint: Any) -> Any:
    """Return a sensible zero-value for a type hint (for missing JSON fields)."""

    origin = typing.get_origin(type_hint)
    args = typing.get_args(type_hint)

    if origin is types.UnionType or origin is typing.Union:
        if type(None) in args:
            return None
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _zero_value(non_none[0])

    if origin is tuple:
        return ()
    if origin in (dict,) or (origin and issubclass(origin, Mapping)):
        return {}
    if type_hint is str:
        return ""
    if type_hint is int:
        return 0
    if type_hint is float:
        return 0.0
    if type_hint is bool:
        return False
    return None


def _coerce(type_hint: Any, value: Any) -> Any:
    """Coerce a JSON-loaded value to match a type hint."""

    if value is None:
        return None

    origin = typing.get_origin(type_hint)
    args = typing.get_args(type_hint)

    if origin is types.UnionType or origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _coerce(non_none[0], value)
        return value

    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        inner = args[0]
        if dataclasses.is_dataclass(inner):
            return tuple(_hydrate(inner, v) for v in value)
        return tuple(value)

    if origin in (dict,) or origin is typing.get_origin(Mapping[str, Any]):
        return value

    if dataclasses.is_dataclass(type_hint):
        return _hydrate(type_hint, value)

    return value


def _emit_success(
    *, catalog: str, run_id: str, task_key: str,
    started_at_ms: int, payload: Mapping[str, Any],
) -> int:
    """Serialize the task result and return exit code 0."""

    completed_at_ms = _now_ms()
    full = {
        "task_key": task_key,
        "run_id": run_id,
        "started_at_ms": started_at_ms,
        "completed_at_ms": completed_at_ms,
        "duration_ms": completed_at_ms - started_at_ms,
        "status": "success",
        **dict(payload),
    }
    _write_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=task_key),
        payload=full,
    )
    print(
        f"[indexer/{task_key}] success run_id={run_id} "
        f"duration_ms={full['duration_ms']}"
    )
    return 0


def _emit_failure(
    *, catalog: str, run_id: str, task_key: str,
    started_at_ms: int, exc: BaseException, reason_code: str,
) -> int:
    """Serialize the failure details and return exit code 1.

    The DAB's task-level retry policy decides whether to retry
    (typically ``max_retries: 2`` with ``min_retry_interval_millis:
    60_000``); after exhausting retries, downstream tasks observe a
    missing ``<task_key>.json`` and themselves fail with
    ``CAPABILITY_GRAPH_UPSTREAM_TASK_FAILED``.
    """

    completed_at_ms = _now_ms()
    payload = {
        "task_key": task_key,
        "run_id": run_id,
        "started_at_ms": started_at_ms,
        "completed_at_ms": completed_at_ms,
        "duration_ms": completed_at_ms - started_at_ms,
        "status": "failed",
        "reason_code": reason_code,
        "error_kind": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(),
    }
    _write_json_artifact(
        path=_staging_error_path(catalog=catalog, run_id=run_id, task_key=task_key),
        payload=payload,
    )
    print(
        f"[indexer/{task_key}] FAILED run_id={run_id} "
        f"reason_code={reason_code} kind={type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    return 1


# ---------------------------------------------------------------------------
# Production wrappers — lazy-loaded protocol implementations
# ---------------------------------------------------------------------------
#
# Every "live" external dependency is hidden behind a tiny class that
# implements a Protocol from the library module. These classes import
# the Databricks SDK / Spark / VS lazily inside their __init__ (or
# inside the first method call) so that this entire file imports
# cleanly on a dev machine without those dependencies.
#
# Each wrapper is ~30 LOC and stateless.


def _build_workspace_client():  # noqa: ANN202 — return type is the SDK class
    """Build a ``databricks.sdk.WorkspaceClient`` lazily.

    Uses default authentication (env / DEFAULT profile / SP token).
    The Job's service principal is ``bv_indexer_sp`` (per
    :func:`brickvision.install.preflight.capability_graph.
    check_indexer_sp_provisioned`); the DAB declares
    ``run_as.service_principal_name: bv_indexer_sp`` so all SDK
    calls execute under that identity.
    """

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]

    return WorkspaceClient()


# ---------------------------------------------------------------------------
# Task functions — one per DAG node
# ---------------------------------------------------------------------------
#
# Each task function:
#  1. Reads its inputs (env vars + upstream JSON artifacts).
#  2. Constructs the production wrapper(s) it needs.
#  3. Calls the corresponding library module.
#  4. Returns a dict (becomes the task's success artifact JSON).
#
# Task functions never call sys.exit themselves; the dispatcher in
# main() handles success/failure exit codes uniformly.


def run_plan(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T01 — write the per-run plan.

    Constructs the snapshot_id, picks today's planned source list
    (per §23.3.6 most refreshes touch all 5 sources; the closed-set
    ``partial_sources`` mechanism lets operators surgically skip
    sources during incident response). Writes a plan.json that
    downstream tasks read for their snapshot_id.
    """

    started_at_ms = _now_ms()
    snapshot_id = f"snap_{run_id}"

    # Closed set of sources for v0.7.7. Partial list is read from
    # an env var override (operator-controlled during incident
    # response); empty == refresh all active sources.
    all_sources = [
        "databricks-sdk-py", "databricks-openapi-aws",
        "databricks-docs-aws",
        "databrickslabs-lakebridge",
    ]
    if os.environ.get("BV_INDEXER_DISABLE_BLOG", "").lower() not in ("1", "true", "yes"):
        all_sources.append("databricks-blog")
    skip_raw = os.environ.get("BV_INDEXER_SKIP_SOURCES", "").strip()
    skip_sources = tuple(s.strip() for s in skip_raw.split(",") if s.strip())
    planned_sources = tuple(s for s in all_sources if s not in skip_sources)

    return {
        "snapshot_id": snapshot_id,
        "planned_at_ms": started_at_ms,
        "refresh_plan_id": f"rp_{run_id}",
        "planned_sources": list(planned_sources),
        "partial_sources": list(skip_sources),
        "indexer_version": "0.7.7",
        "_started_at_ms": started_at_ms,
    }


def run_sdk(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T02 — parse the databricks-sdk-py source tree.

    Clones the repo at runtime if BV_INDEXER_SDK_ROOT is not set.
    """

    from brickvision_runtime.capability_graph.sources import sdk_adapter

    started_at_ms = _now_ms()
    sdk_root = os.environ.get("BV_INDEXER_SDK_ROOT", "").strip()
    if not sdk_root:
        sdk_root = _clone_repo(
            "https://github.com/databricks/databricks-sdk-py.git",
            shallow=True,
        )
    result = sdk_adapter.parse_sdk(sdk_root=sdk_root, parsed_at_ms=started_at_ms)

    return {
        "modules": [dataclasses.asdict(m) for m in result.modules],
        "services": [dataclasses.asdict(s) for s in result.services],
        "methods": [dataclasses.asdict(m) for m in result.methods],
        "parse_errors": [dataclasses.asdict(e) for e in result.parse_errors],
        "sdk_version": result.sdk_version,
        "_started_at_ms": started_at_ms,
    }


def _fetch_openapi_from_api_sitemap(cloud: str) -> list[Any]:
    """Fetch per-cloud API reference pages from the Databricks API sitemap.

    The API sitemap at docs.databricks.com/api/sitemap.xml contains all
    workspace and account API operation URLs. Each cloud's docs sitemap
    also lists API reference pages under /dev-tools/api/ paths.

    We fetch the unified API sitemap (cloud-agnostic, ~3K URLs) then
    also supplement with any cloud-specific pages from the per-cloud
    docs sitemap that reference REST API concepts.
    """

    from brickvision_runtime.capability_graph.sources import openapi_adapter  # noqa: PLC0415

    api_sitemap_url = "https://docs.databricks.com/api/sitemap.xml"
    all_urls = _fetch_sitemap_urls(api_sitemap_url)

    cloud_docs_sitemaps = {
        "aws": "https://docs.databricks.com/aws/en/sitemap.xml",
        "azure": "https://docs.databricks.com/azure/en/sitemap.xml",
        "gcp": "https://docs.databricks.com/gcp/en/sitemap.xml",
    }
    cloud_sitemap = cloud_docs_sitemaps.get(cloud, "")
    if cloud_sitemap:
        cloud_urls = _fetch_sitemap_urls(cloud_sitemap)
        api_ref_urls = [u for u in cloud_urls if "/dev-tools/api/" in u or "/rest-api/" in u.lower()]
        all_urls.extend(api_ref_urls)

    all_urls = list(dict.fromkeys(all_urls))

    cloud_filter_map = {
        "aws": lambda u: "/azure/" not in u and "/gcp/" not in u,
        "azure": lambda u: "/aws/" not in u and "/gcp/" not in u,
        "gcp": lambda u: "/aws/" not in u and "/azure/" not in u,
    }
    url_filter = cloud_filter_map.get(cloud, lambda _u: True)
    all_urls = [u for u in all_urls if url_filter(u)]

    service_urls: list[str] = []
    for u in all_urls:
        parts = u.rstrip("/").split("/")
        if len(parts) >= 5 and parts[-2] not in ("workspace", "account", "introduction"):
            service_urls.append(u)

    fetched = _fetch_pages_batch(service_urls, delay_sec=0.1)

    documents: list[openapi_adapter.OpenAPIDocumentInput] = []
    for url, html in fetched:
        parts = url.rstrip("/").split("/")
        if "workspace" in parts:
            idx = parts.index("workspace")
            api_version = "workspace"
        elif "account" in parts:
            idx = parts.index("account")
            api_version = "account"
        else:
            api_version = "unknown"
            idx = -1

        if idx >= 0 and idx + 1 < len(parts):
            service_name = parts[idx + 1]
            operation_name = parts[idx + 2] if idx + 2 < len(parts) else service_name
        else:
            service_name = parts[-2] if len(parts) >= 2 else "unknown"
            operation_name = parts[-1] if parts else "unknown"

        canonical = _canonical_api_reference_operation(service_name, operation_name)
        if canonical is None:
            continue
        http_method, api_path, operation_id, api_version = canonical

        doc: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": f"Databricks {cloud.upper()} API - {service_name}", "version": api_version},
            "paths": {
                api_path: {
                    http_method.lower(): {
                        "operationId": operation_id,
                        "summary": _extract_title_from_html(html),
                        "description": _extract_description_from_html(html),
                        "tags": [service_name, cloud],
                    }
                }
            },
            "components": {"schemas": {}, "securitySchemes": {}},
        }
        documents.append(
            openapi_adapter.OpenAPIDocumentInput(
                document=doc,
                api_version=api_version,
                source_url=url,
            )
        )

    return _dedupe_openapi_documents(documents)


def _dedupe_openapi_documents(documents: list[Any]) -> list[Any]:
    """Collapse API-reference aliases onto one canonical operation document.

    The API sitemap can expose both legacy and versioned pages for the same
    REST operation, e.g. ``workspace/jobs/create`` and
    ``workspace/jobs_21/create``. Account and workspace APIs remain distinct:
    the dedupe key is the canonical REST method/path/version emitted by
    ``_canonical_api_reference_operation``.
    """

    passthrough: list[Any] = []
    by_key: dict[tuple[str, str, str, str], Any] = {}
    for doc_input in documents:
        key = _openapi_document_canonical_key(doc_input)
        if key is None:
            passthrough.append(doc_input)
            continue
        existing = by_key.get(key)
        if existing is None or _openapi_document_preference(doc_input) > _openapi_document_preference(existing):
            by_key[key] = doc_input
    return passthrough + [by_key[key] for key in sorted(by_key)]


def _openapi_document_canonical_key(doc_input: Any) -> tuple[str, str, str, str] | None:
    document = getattr(doc_input, "document", None)
    if not isinstance(document, dict):
        return None
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        return None
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(method, str) or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if isinstance(operation_id, str):
                return (str(getattr(doc_input, "api_version", "")), method.upper(), path, operation_id)
    return None


def _openapi_document_preference(doc_input: Any) -> tuple[int, int, str]:
    """Prefer current/versioned API pages over legacy alias pages."""

    source_url = str(getattr(doc_input, "source_url", "") or "")
    parts = source_url.rstrip("/").split("/")
    service = ""
    if "workspace" in parts:
        idx = parts.index("workspace")
        service = parts[idx + 1] if idx + 1 < len(parts) else ""
    elif "account" in parts:
        idx = parts.index("account")
        service = parts[idx + 1] if idx + 1 < len(parts) else ""
    service = service.lower()
    explicit_version_alias = 1 if re.search(r"(?:^|_)\d+(?:_\d+)?$", service) else 0
    current_product_name = 1 if service.startswith(("lakeflow", "mosaic", "unity")) else 0
    return (explicit_version_alias, current_product_name, source_url)


def _canonical_api_reference_operation(
    service_name: str,
    operation_name: str,
) -> tuple[str, str, str, str] | None:
    """Map docs API reference slugs to canonical REST method/path/version.

    The docs API sitemap uses page slugs such as
    ``/api/workspace/statementexecution/executestatement``. Those pages are
    useful source artifacts, but the graph routes on canonical REST paths.
    """

    service = service_name.strip().lower().replace("-", "").replace("_", "")
    raw_service = service_name.strip().lower()
    operation = operation_name.strip().lower().replace("-", "").replace("_", "")
    raw_operation = operation_name.strip().lower()

    if raw_service in {"jobs_21", "jobs21", "jobs"}:
        version = "2.1"
        op_slug = raw_operation.replace("_", "-")
        known_jobs: dict[str, tuple[str, str, str]] = {
            "submit": ("POST", "/api/2.1/jobs/runs/submit", "jobs-runs-submit"),
            "run-now": ("POST", "/api/2.1/jobs/run-now", "jobs-run-now"),
            "runnow": ("POST", "/api/2.1/jobs/run-now", "jobs-run-now"),
            "create": ("POST", "/api/2.1/jobs/create", "jobs-create"),
            "get": ("GET", "/api/2.1/jobs/get", "jobs-get"),
            "list": ("GET", "/api/2.1/jobs/list", "jobs-list"),
            "getrun": ("GET", "/api/2.1/jobs/runs/get", "jobs-runs-get"),
            "cancelrun": ("POST", "/api/2.1/jobs/runs/cancel", "jobs-runs-cancel"),
            "deleterun": ("POST", "/api/2.1/jobs/runs/delete", "jobs-runs-delete"),
            "listruns": ("GET", "/api/2.1/jobs/runs/list", "jobs-runs-list"),
            "getrunoutput": ("GET", "/api/2.1/jobs/runs/get-output", "jobs-runs-get-output"),
        }
        method, path, op_id = known_jobs.get(
            op_slug,
            (_verb_to_http_method(operation), f"/api/2.1/jobs/{op_slug}", f"jobs-{op_slug}"),
        )
        return method, path, op_id, version

    if service == "statementexecution":
        version = "2.0"
        known_statement: dict[str, tuple[str, str, str]] = {
            "executestatement": (
                "POST",
                "/api/2.0/sql/statements",
                "statementexecution_executestatement",
            ),
            "cancelexecution": (
                "POST",
                "/api/2.0/sql/statements/{statement_id}/cancel",
                "statementexecution_cancelexecution",
            ),
            "getstatement": (
                "GET",
                "/api/2.0/sql/statements/{statement_id}",
                "statementexecution_getstatement",
            ),
            "getstatementresultchunkn": (
                "GET",
                "/api/2.0/sql/statements/{statement_id}/result/chunks/{chunk_index}",
                "statementexecution_getstatementresultchunkn",
            ),
        }
        return (*known_statement[operation], version) if operation in known_statement else None

    if service == "registeredmodels":
        version = "2.0"
        alias_paths: dict[str, tuple[str, str, str]] = {
            "setalias": (
                "POST",
                "/api/2.0/mlflow/registered-models/alias",
                "registeredmodels_setalias",
            ),
            "deletealias": (
                "DELETE",
                "/api/2.0/mlflow/registered-models/alias",
                "registeredmodels_deletealias",
            ),
        }
        if operation in alias_paths:
            return (*alias_paths[operation], version)
        op_slug = raw_operation.replace("_", "-")
        return (
            _verb_to_http_method(operation),
            f"/api/2.0/mlflow/registered-models/{op_slug}",
            f"registeredmodels_{raw_operation}",
            version,
        )

    if service == "modelversions":
        version = "2.0"
        op_slug = raw_operation.replace("_", "-")
        return (
            _verb_to_http_method(operation),
            f"/api/2.0/mlflow/model-versions/{op_slug}",
            f"modelversions_{raw_operation}",
            version,
        )

    if service in {"servingendpoints", "servingendpoint"}:
        version = "2.0"
        op_slug = raw_operation.replace("_", "-")
        return (
            _verb_to_http_method(operation),
            f"/api/2.0/serving-endpoints/{op_slug}",
            f"servingendpoints_{raw_operation}",
            version,
        )

    if service == "experiments":
        version = "2.0"
        op_slug = raw_operation.replace("_", "-")
        return (
            _verb_to_http_method(operation),
            f"/api/2.0/mlflow/experiments/{op_slug}",
            f"experiments_{raw_operation}",
            version,
        )

    return None


def _verb_to_http_method(operation: str) -> str:
    if operation.startswith(("get", "list", "search")):
        return "GET"
    if operation.startswith("delete"):
        return "DELETE"
    if operation.startswith(("update", "patch", "edit")):
        return "PATCH"
    return "POST"


def _extract_title_from_html(html: str) -> str:
    """Extract page title from HTML."""
    import re  # noqa: PLC0415

    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip().split("|")[0].strip()
    m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_description_from_html(html: str) -> str:
    """Extract meta description or first paragraph from HTML."""
    import re  # noqa: PLC0415

    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"<p[^>]*>(.{20,300}?)</p>", html, re.IGNORECASE | re.DOTALL)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1))
        return text.strip()
    return ""


def _run_openapi_cloud(cloud: str, *, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Shared implementation for per-cloud OpenAPI tasks."""

    from brickvision_runtime.capability_graph.sources import openapi_adapter

    started_at_ms = _now_ms()
    documents = _fetch_openapi_from_api_sitemap(cloud)

    result = openapi_adapter.parse_openapi(
        documents=documents, parsed_at_ms=started_at_ms,
    )

    return {
        "cloud": cloud,
        "documents": [dataclasses.asdict(d) for d in result.documents],
        "operations": [dataclasses.asdict(o) for o in result.operations],
        "schemas": [dataclasses.asdict(s) for s in result.schemas],
        "security_schemes": [dataclasses.asdict(s) for s in result.security_schemes],
        "parse_errors": [dataclasses.asdict(e) for e in result.parse_errors],
        "_started_at_ms": started_at_ms,
    }


def run_openapi_aws(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T03a — parse Databricks OpenAPI for AWS cloud."""
    return _run_openapi_cloud("aws", catalog=catalog, run_id=run_id)


def run_openapi_azure(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T03b — parse Databricks OpenAPI for Azure cloud."""
    return _run_openapi_cloud("azure", catalog=catalog, run_id=run_id)


def run_openapi_gcp(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T03c — parse Databricks OpenAPI for GCP cloud."""
    return _run_openapi_cloud("gcp", catalog=catalog, run_id=run_id)


def _run_docs_cloud(cloud: str, *, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Shared implementation for per-cloud docs tasks."""

    from brickvision_runtime.capability_graph.sources import docs_adapter

    started_at_ms = _now_ms()
    max_pages = _read_env_int("BV_INDEXER_DOCS_MAX_PAGES", default=0)
    pages = _fetch_docs_pages_for_cloud(cloud, max_pages=max_pages)

    result = docs_adapter.parse_docs(
        pages=pages, parsed_at_ms=started_at_ms,
    )

    return {
        "cloud": cloud,
        "corpora": [dataclasses.asdict(c) for c in result.corpora],
        "section_roots": [dataclasses.asdict(s) for s in result.section_roots],
        "pages": [dataclasses.asdict(p) for p in result.pages],
        "chunks": [dataclasses.asdict(c) for c in result.chunks],
        "parse_errors": [dataclasses.asdict(e) for e in result.parse_errors],
        "_started_at_ms": started_at_ms,
    }


def run_docs_aws(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T04a — parse Databricks docs for AWS cloud."""
    return _run_docs_cloud("aws", catalog=catalog, run_id=run_id)


def run_docs_azure(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T04b — parse Databricks docs for Azure cloud."""
    return _run_docs_cloud("azure", catalog=catalog, run_id=run_id)


def run_docs_gcp(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T04c — parse Databricks docs for GCP cloud."""
    return _run_docs_cloud("gcp", catalog=catalog, run_id=run_id)


def run_blog(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T05 — parse Databricks blog HTML posts.

    Fetches posts from databricks.com/blog at runtime if
    BV_INDEXER_BLOG_POSTS_DIR is not set.

    Skipped entirely when BV_INDEXER_DISABLE_BLOG=true.
    """

    if os.environ.get("BV_INDEXER_DISABLE_BLOG", "").lower() in ("1", "true", "yes"):
        return {"chunks": [], "posts_parsed": 0, "skipped": True, "reason": "BV_INDEXER_DISABLE_BLOG=true"}

    from brickvision_runtime.capability_graph.sources import blog_adapter

    started_at_ms = _now_ms()
    posts_dir = os.environ.get("BV_INDEXER_BLOG_POSTS_DIR", "").strip()

    posts: list[blog_adapter.BlogPostInput] = []
    if posts_dir and Path(posts_dir).is_dir():
        for html_path in sorted(Path(posts_dir).glob("*.html")):
            meta_path = html_path.with_suffix(".meta.json")
            if not meta_path.exists():
                continue
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            with open(html_path, encoding="utf-8") as fh:
                html = fh.read()
            posts.append(
                blog_adapter.BlogPostInput(
                    url=str(meta["url"]),
                    html=html,
                    fetched_at_ms=int(meta["fetched_at_ms"]),
                )
            )
    else:
        # Runtime fetch — crawl blog posts from sitemap
        max_posts = _read_env_int("BV_INDEXER_BLOG_MAX_POSTS", default=0)
        posts = _fetch_blog_posts(max_posts=max_posts)

    result = blog_adapter.parse_blog(
        posts=posts, parsed_at_ms=started_at_ms,
    )

    return {
        "corpus": dataclasses.asdict(result.corpus) if result.corpus else None,
        "posts": [dataclasses.asdict(p) for p in result.posts],
        "chunks": [dataclasses.asdict(c) for c in result.chunks],
        "parse_errors": [dataclasses.asdict(e) for e in result.parse_errors],
        "_started_at_ms": started_at_ms,
    }


def run_labs(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T06 — parse the databrickslabs/lakebridge repo.

    Clones the repo at runtime if BV_INDEXER_LABS_REPO_ROOT is not set.
    """

    from brickvision_runtime.capability_graph.sources import labs_repo_adapter

    started_at_ms = _now_ms()
    repo_root = os.environ.get("BV_INDEXER_LABS_REPO_ROOT", "").strip()
    if not repo_root:
        repo_root = _clone_repo(
            "https://github.com/databrickslabs/lakebridge.git",
            shallow=True,
        )
    repo_revision = os.environ.get("BV_INDEXER_LABS_REVISION", "").strip() or None

    result = labs_repo_adapter.parse_lakebridge(
        repo_root=repo_root, parsed_at_ms=started_at_ms,
        repo_revision=repo_revision,
    )

    return {
        "repo": dataclasses.asdict(result.repo) if result.repo else None,
        "modules": [dataclasses.asdict(m) for m in result.modules],
        "classes": [dataclasses.asdict(c) for c in result.classes],
        "callables": [dataclasses.asdict(c) for c in result.callables],
        "parse_errors": [dataclasses.asdict(e) for e in result.parse_errors],
        "_started_at_ms": started_at_ms,
    }


def _merge_docs_results(*results: Any) -> Any:
    """Merge multiple per-cloud DocsAdapterResult objects into one."""

    from brickvision_runtime.capability_graph.sources import docs_adapter  # noqa: PLC0415

    non_none = [r for r in results if r is not None]
    if not non_none:
        return None

    corpora: list[Any] = []
    section_roots: list[Any] = []
    pages: list[Any] = []
    chunks: list[Any] = []
    parse_errors: list[Any] = []
    skipped: list[str] = []
    corpus_partial: dict[str, tuple[int, int]] = {}
    parsed_at_ms = 0

    for r in non_none:
        corpora.extend(r.corpora)
        section_roots.extend(r.section_roots)
        pages.extend(r.pages)
        chunks.extend(r.chunks)
        parse_errors.extend(r.parse_errors)
        skipped.extend(r.skipped_non_skill_bearing)
        for k, v in r.corpus_partial_summary.items():
            if k in corpus_partial:
                old = corpus_partial[k]
                corpus_partial[k] = (old[0] + v[0], old[1] + v[1])
            else:
                corpus_partial[k] = v
        if r.parsed_at_ms > parsed_at_ms:
            parsed_at_ms = r.parsed_at_ms

    return docs_adapter.DocsAdapterResult(
        parsed_at_ms=parsed_at_ms,
        corpora=tuple(corpora),
        section_roots=tuple(section_roots),
        pages=tuple(pages),
        chunks=tuple(chunks),
        parse_errors=tuple(parse_errors),
        skipped_non_skill_bearing=tuple(skipped),
        corpus_partial_summary=corpus_partial,
    )


def _merge_openapi_results(*results: Any) -> Any:
    """Merge multiple per-cloud OpenAPIAdapterResult objects into one.

    If all inputs are None, returns None (graph_builder accepts None for
    missing sources). Otherwise merges documents, operations, schemas,
    security_schemes, and parse_errors from all non-None results.
    """

    from brickvision_runtime.capability_graph.sources import openapi_adapter  # noqa: PLC0415

    non_none = [r for r in results if r is not None]
    if not non_none:
        return None

    documents: list[Any] = []
    operations: list[Any] = []
    schemas: list[Any] = []
    security_schemes: list[Any] = []
    parse_errors: list[Any] = []
    parsed_at_ms = 0

    for r in non_none:
        documents.extend(r.documents)
        operations.extend(r.operations)
        schemas.extend(r.schemas)
        security_schemes.extend(r.security_schemes)
        parse_errors.extend(r.parse_errors)
        if r.parsed_at_ms > parsed_at_ms:
            parsed_at_ms = r.parsed_at_ms

    return openapi_adapter.OpenAPIAdapterResult(
        parsed_at_ms=parsed_at_ms,
        documents=tuple(documents),
        operations=tuple(operations),
        schemas=tuple(schemas),
        security_schemes=tuple(security_schemes),
        parse_errors=tuple(parse_errors),
    )


def _load_hand_authored_skill_specs(graph_builder: Any) -> tuple[Any, ...]:
    """Load the closed hand-authored exemplar catalog for graph linking.

    Production installs may provide a JSON manifest, but bundle deploys also
    carry the repository's ``skills/`` directory. Falling back to that directory
    prevents the indexer from silently publishing snapshots with zero exemplar
    links when the optional manifest variable is omitted.
    """

    manifest_path = os.environ.get("BV_INDEXER_HAND_AUTHORED_MANIFEST", "").strip()
    if manifest_path:
        path = Path(manifest_path)
        if not path.exists():
            raise RuntimeError(
                "BV_INDEXER_HAND_AUTHORED_MANIFEST is set but does not exist: "
                f"{path}"
            )
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        specs = tuple(
            graph_builder.HandAuthoredSkillSpec(
                skill_id=str(row["skill_id"]),
                exemplar_of=str(row["exemplar_of"]),
                title=str(row.get("title") or "") or None,
            )
            for row in rows
        )
        if specs:
            return specs
        raise RuntimeError("hand-authored skill manifest is empty")

    from brickvision_runtime.capability_graph.exemplars import load_skill  # noqa: PLC0415

    candidates: list[Path] = []
    skills_dir_env = os.environ.get("BV_INDEXER_SKILLS_DIR", "").strip()
    if skills_dir_env:
        candidates.append(Path(skills_dir_env))
    if _SRC_ROOT:
        candidates.append(Path(_SRC_ROOT).parent / "skills")
    candidates.append(Path.cwd() / "skills")

    for skills_dir in candidates:
        if not skills_dir.is_dir():
            continue
        specs: list[Any] = []
        for folder in sorted(skills_dir.iterdir()):
            if not folder.is_dir() or not (folder / "SKILL.yaml").is_file():
                continue
            skill = load_skill(folder)
            specs.append(
                graph_builder.HandAuthoredSkillSpec(
                    skill_id=skill.skill_id,
                    exemplar_of=skill.exemplar_of,
                    title=str(skill.ir.get("title") or "") or None,
                )
            )
        if specs:
            return tuple(specs)

    searched = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        "no hand-authored skills found for Capability Graph exemplar linkage; "
        f"searched: {searched}"
    )


def run_graph_builder(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T07 — merge all 5 source results into the capability graph.

    Reads the 5 upstream task JSONs from staging, reconstructs typed
    result objects via :func:`_hydrate`, loads any hand-authored
    skill exemplars from the install-provided manifest, and invokes
    :func:`graph_builder.build_capability_graph`.

    Per §23.3.1 the merge step accepts ``None`` for any missing
    source (when the operator listed it in ``BV_INDEXER_SKIP_SOURCES``);
    the partial-source list is propagated to the corpus_snapshots
    row downstream so retrieval can flag stale source coverage.
    """

    from brickvision_runtime.capability_graph import graph_builder
    from brickvision_runtime.capability_graph.sources import (
        blog_adapter, docs_adapter, labs_repo_adapter,
        openapi_adapter, sdk_adapter,
    )

    started_at_ms = _now_ms()
    plan = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
    )
    snapshot_id = plan["snapshot_id"]
    planned_sources = plan.get("planned_sources", [])

    def _maybe_load(task_key: str, result_cls: type) -> Any:
        """Hydrate an adapter result iff its source was planned for
        today's refresh; ``None`` otherwise (graph_builder accepts None
        per the partial-run contract)."""

        source_name_map = {
            _TASK_KEY_SDK: "databricks-sdk-py",
            _TASK_KEY_OPENAPI_AWS: "databricks-openapi-aws",
            _TASK_KEY_OPENAPI_AZURE: "databricks-openapi-azure",
            _TASK_KEY_OPENAPI_GCP: "databricks-openapi-gcp",
            _TASK_KEY_DOCS_AWS: "databricks-docs-aws",
            _TASK_KEY_DOCS_AZURE: "databricks-docs-azure",
            _TASK_KEY_DOCS_GCP: "databricks-docs-gcp",
            _TASK_KEY_BLOG: "databricks-blog",
            _TASK_KEY_LABS: "databrickslabs-lakebridge",
        }
        if source_name_map[task_key] not in planned_sources:
            return None
        staging_path = _staging_path(catalog=catalog, run_id=run_id, task_key=task_key)
        if not Path(staging_path).exists():
            return None
        artifact = _read_json_artifact(path=staging_path)
        return _hydrate(result_cls, artifact)

    sdk_result = _maybe_load(_TASK_KEY_SDK, sdk_adapter.SDKAdapterResult)

    openapi_aws = _maybe_load(_TASK_KEY_OPENAPI_AWS, openapi_adapter.OpenAPIAdapterResult)
    openapi_azure = _maybe_load(_TASK_KEY_OPENAPI_AZURE, openapi_adapter.OpenAPIAdapterResult)
    openapi_gcp = _maybe_load(_TASK_KEY_OPENAPI_GCP, openapi_adapter.OpenAPIAdapterResult)
    openapi_result = _merge_openapi_results(openapi_aws, openapi_azure, openapi_gcp)

    docs_aws = _maybe_load(_TASK_KEY_DOCS_AWS, docs_adapter.DocsAdapterResult)
    docs_azure = _maybe_load(_TASK_KEY_DOCS_AZURE, docs_adapter.DocsAdapterResult)
    docs_gcp = _maybe_load(_TASK_KEY_DOCS_GCP, docs_adapter.DocsAdapterResult)
    docs_result = _merge_docs_results(docs_aws, docs_azure, docs_gcp)

    blog_result = _maybe_load(_TASK_KEY_BLOG, blog_adapter.BlogAdapterResult)
    labs_result = _maybe_load(_TASK_KEY_LABS, labs_repo_adapter.LabsAdapterResult)

    hand_authored = _load_hand_authored_skill_specs(graph_builder)

    result = graph_builder.build_capability_graph(
        sdk_result=sdk_result,
        openapi_result=openapi_result,
        docs_result=docs_result,
        blog_result=blog_result,
        labs_result=labs_result,
        hand_authored_skills=hand_authored,
        snapshot_id=snapshot_id,
        built_at_ms=started_at_ms,
        now_ms=started_at_ms,
        corpus_hash=plan.get("corpus_hash", ""),
    )

    return {
        "snapshot_id": result.snapshot_id,
        "built_at_ms": result.built_at_ms,
        "top_orders": [dataclasses.asdict(t) for t in result.top_orders],
        "meta_skills": [dataclasses.asdict(m) for m in result.meta_skills],
        "extensions": [dataclasses.asdict(e) for e in result.extensions],
        "entity_edges": [dataclasses.asdict(e) for e in result.entity_edges],
        "source_provenance": [dataclasses.asdict(s) for s in result.source_provenance],
        "unlinked_entities": list(result.unlinked_entities),
        "broken_exemplar_pointers": [
            dataclasses.asdict(b) for b in result.broken_exemplar_pointers
        ],
        "build_telemetry": dict(result.build_telemetry),
        "_started_at_ms": started_at_ms,
    }


def run_embed(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T08 — generate embeddings via Foundation Model Serving.

    Builds one :class:`embed.EmbedRequest` per text-bearing entity:
      * docs chunks (text from docs_adapter.json)
      * blog chunks (text from blog_adapter.json)
      * extensions (synopsis text from graph_builder.json)
      * top-order titles + descriptions (for top-level navigation queries)

    Looks each request up in ``<BV_CATALOG>.<BV_SCHEMA>.embedding_cache``
    via a Spark-backed lookup, calls FMS for the misses, and writes
    the new ``EmbeddingCacheRow``s back to Delta.
    """

    from brickvision_runtime.capability_graph import embed
    from brickvision_runtime.capability_graph.schemas.types import EmbeddingCacheRow

    started_at_ms = _now_ms()
    embedding_endpoint = _read_env(
        "LLM_EMBEDDING_TASKS",
        default=_read_env("BV_INDEXER_EMBEDDING_ENDPOINT", default=_DEFAULT_EMBEDDING_ENDPOINT),
    )
    daily_token_cap = _read_env_int(
        "BV_INDEXER_DAILY_TOKEN_CAP", default=5_000_000,
    )
    daily_budget_usd = _read_env_float(
        "BV_INDEXER_DAILY_EMBEDDING_BUDGET_USD", default=50.0,
    )

    requests: list[embed.EmbedRequest] = []
    seen_hashes: set[str] = set()

    def _add(content_hash: str, text: str) -> None:
        if content_hash in seen_hashes or not text:
            return
        seen_hashes.add(content_hash)
        requests.append(embed.EmbedRequest(content_hash=content_hash, text=text))

    plan = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
    )
    planned = set(plan.get("planned_sources", []))

    if "databricks-docs-aws" in planned:
        for chunk in _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_DOCS_AWS),
        ).get("chunks", []):
            _add(chunk["content_hash"], chunk["chunk_text"])
    if "databricks-docs-azure" in planned:
        for chunk in _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_DOCS_AZURE),
        ).get("chunks", []):
            _add(chunk["content_hash"], chunk["chunk_text"])
    if "databricks-docs-gcp" in planned:
        for chunk in _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_DOCS_GCP),
        ).get("chunks", []):
            _add(chunk["content_hash"], chunk["chunk_text"])
    if "databricks-blog" in planned:
        _blog_path = _staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_BLOG)
        if Path(_blog_path).exists():
            for chunk in _read_json_artifact(path=_blog_path).get("chunks", []):
                _add(chunk["content_hash"], chunk["chunk_text"])

    graph = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_GRAPH_BUILDER),
    )
    for ext in graph["extensions"]:
        _add(
            ext["last_indexed_corpus_hash"],
            f"{ext['title']}\n\n{ext['synopsis']}\n\n{ext['when_to_use']}",
        )

    # Batch cache lookup — load all existing hashes in one Spark query
    # instead of 1 query per item.
    from pyspark.sql import SparkSession  # type: ignore[import-not-found]
    spark = SparkSession.builder.getOrCreate()

    cache_table = f"{catalog}.{_resolve_schema()}.embedding_cache"

    all_hashes = [r.content_hash for r in requests]
    cache_map: dict[str, EmbeddingCacheRow] = {}
    if all_hashes:
        hash_list_sql = ",".join(f"'{h}'" for h in all_hashes)
        df = spark.sql(
            f"SELECT * FROM {cache_table} WHERE content_hash IN ({hash_list_sql})"
        )
        for r in df.collect():
            cache_map[r["content_hash"]] = EmbeddingCacheRow(
                content_hash=r["content_hash"],
                embedding_endpoint=r["embedding_endpoint"],
                embedding_dim=r["embedding_dim"],
                embedding=tuple(r["embedding"]),
                emitted_at_ms=r["emitted_at_ms"],
                last_used_at_ms=r["last_used_at_ms"],
            )

    def _cache_lookup(content_hash: str) -> EmbeddingCacheRow | None:
        return cache_map.get(content_hash)

    result = embed.embed_batch(
        requests=requests, cache_lookup=_cache_lookup,
        embedding_endpoint=embedding_endpoint,
        embedding_dim=_DEFAULT_EMBEDDING_DIM,
        daily_token_cap=daily_token_cap,
        daily_budget_usd=daily_budget_usd,
        parsed_at_ms=started_at_ms,
        sleep=time.sleep,
    )

    # Fail the task if no embeddings were produced but items were requested
    if not result.rows and result.cache_misses > 0:
        error_sample = "; ".join(
            f"{e.error_kind}: {e.error_message[:80]}" for e in result.errors[:3]
        )
        raise RuntimeError(
            f"Embedding failed: 0 rows produced from {result.cache_misses} misses. "
            f"Errors ({len(result.errors)}): {error_sample}"
        )

    # Write new/updated rows to Delta
    if result.rows:
        new_rows = [dataclasses.asdict(r) for r in result.rows]
        spark.createDataFrame(new_rows).write.format("delta").mode(
            "overwrite"
        ).option("overwriteSchema", "true").saveAsTable(cache_table)

    return {
        "rows": [dataclasses.asdict(r) for r in result.rows],
        "cache_hits": result.cache_hits,
        "cache_misses": result.cache_misses,
        "network_calls": result.network_calls,
        "estimated_token_count": result.estimated_token_count,
        "estimated_cost_usd": result.estimated_cost_usd,
        "retries": result.retries,
        "errors": [dataclasses.asdict(e) for e in result.errors[:10]],
        "truncated_at_request_index": result.truncated_at_request_index,
        "_started_at_ms": started_at_ms,
    }


def run_persist(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T09 — write the snapshot to Delta tables."""

    from brickvision_runtime.capability_graph import graph_builder, persist
    from brickvision_runtime.capability_graph.schemas.types import (
        CorpusHealthRow,
        CorpusSnapshotRow,
        RefreshPlanRow,
        SourceAuthorityRow,
    )

    started_at_ms = _now_ms()
    plan = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
    )
    graph = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_GRAPH_BUILDER),
    )

    build_result = _hydrate(graph_builder.CapabilityGraphBuildResult, graph)

    corpus_snapshot = CorpusSnapshotRow(
        snapshot_id=plan["snapshot_id"],
        corpus_hash=plan.get("corpus_hash", ""),
        refresh_plan_id=plan["refresh_plan_id"],
        planned_at_ms=plan["planned_at_ms"],
        partial_sources=tuple(plan.get("partial_sources", [])),
        signed_by=_read_env("BV_INDEXER_SP_NAME", default="bv_indexer_sp"),
        signature=None,  # promote.py stamps this after smoke passes
        promoted_at_ms=None,
        deactivated_at_ms=None,
        task_durations_ms_json=json.dumps({}),  # promote.py stamps this too
    )

    partial_sources = tuple(plan.get("partial_sources", []))
    planned_sources = tuple(plan.get("planned_sources", []))
    refresh_plan = RefreshPlanRow(
        refresh_plan_id=plan["refresh_plan_id"],
        planned_at_ms=int(plan["planned_at_ms"]),
        planned_sources=planned_sources,
        partial_sources=partial_sources,
        sdk_version=_sdk_version_from_staging(catalog=catalog, run_id=run_id),
        daily_token_cap=_int_env("BV_INDEXER_DAILY_TOKEN_CAP", default=0),
        daily_embedding_budget_usd=_float_env(
            "BV_INDEXER_DAILY_EMBEDDING_BUDGET_USD", default=0.0,
        ),
        freshness_tolerance_days=_int_env(
            "BV_INDEXER_FRESHNESS_TOLERANCE_DAYS", default=2,
        ),
        triggered_by=_read_env("BV_INDEXER_TRIGGERED_BY", default="manual"),
        result_status="partial" if partial_sources else "success",
        result_snapshot_id=plan["snapshot_id"],
        duration_ms=max(0, started_at_ms - int(plan["planned_at_ms"])),
        embedding_cost_usd=0.0,
    )
    corpus_health_rows = _build_corpus_health_rows(
        plan=plan,
        graph=graph,
        recorded_at_ms=started_at_ms,
    )

    result = persist.persist_snapshot(
        build_result=build_result, corpus_snapshot=corpus_snapshot,
        refresh_plan_rows=(refresh_plan,),
        source_authority_rows=_source_authority_rows(enacted_at_ms=started_at_ms),
        corpus_health_rows=corpus_health_rows,
        catalog=catalog, started_at_ms=started_at_ms,
        completed_at_ms=_now_ms(),
    )

    return {
        "snapshot_id": result.snapshot_id,
        "rows_written_per_table": dict(result.rows_written_per_table),
        "duration_ms": result.duration_ms,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "_started_at_ms": started_at_ms,
    }


def _int_env(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, *, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _sdk_version_from_staging(*, catalog: str, run_id: str) -> str | None:
    path = Path(_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_SDK))
    if not path.exists():
        return None
    return str(_read_json_artifact(path=str(path)).get("sdk_version") or "") or None


def _source_authority_rows(*, enacted_at_ms: int) -> tuple[SourceAuthorityRow, ...]:
    from brickvision_runtime.capability_graph.schemas.types import SourceAuthorityRow  # noqa: PLC0415

    return (
        SourceAuthorityRow(1, "sdk", 1.00, "Databricks SDK source adapter", enacted_at_ms),
        SourceAuthorityRow(1, "openapi", 0.95, "Databricks OpenAPI source adapter", enacted_at_ms),
        SourceAuthorityRow(1, "docs", 0.85, "Databricks documentation source adapter", enacted_at_ms),
        SourceAuthorityRow(1, "labs", 0.75, "Databricks Labs repository source adapter", enacted_at_ms),
        SourceAuthorityRow(1, "blog", 0.50, "Databricks blog source adapter", enacted_at_ms),
        SourceAuthorityRow(
            1,
            "hand_authored",
            0.00,
            "BrickVision hand-authored skill contract provenance (not capability evidence)",
            enacted_at_ms,
        ),
    )


def _build_corpus_health_rows(
    *, plan: Mapping[str, Any], graph: Mapping[str, Any], recorded_at_ms: int,
) -> tuple[CorpusHealthRow, ...]:
    from brickvision_runtime.capability_graph.schemas.types import CorpusHealthRow  # noqa: PLC0415

    planned_sources = tuple(str(s) for s in plan.get("planned_sources", []))
    partial_sources = tuple(str(s) for s in plan.get("partial_sources", []))
    source_kind_by_planned_source = {
        "databricks-sdk-py": "sdk",
        "databricks-openapi-aws": "openapi",
        "databricks-openapi-azure": "openapi",
        "databricks-openapi-gcp": "openapi",
        "databricks-docs-aws": "docs",
        "databricks-docs-azure": "docs",
        "databricks-docs-gcp": "docs",
        "databricks-blog": "blog",
        "databrickslabs-lakebridge": "labs",
    }
    planned_kinds = {
        source_kind_by_planned_source[source]
        for source in planned_sources
        if source in source_kind_by_planned_source
    }
    partial_kinds = {
        source_kind_by_planned_source[source]
        for source in partial_sources
        if source in source_kind_by_planned_source
    }
    counts: dict[str, int] = {kind: 0 for kind in planned_kinds}
    for row in graph.get("source_provenance", []):
        kind = str(row.get("source_kind") or "")
        if kind in counts:
            counts[kind] += 1

    return tuple(
        CorpusHealthRow(
            recorded_at_ms=recorded_at_ms,
            source_kind=kind,
            last_refresh_at_ms=recorded_at_ms,
            last_refresh_duration_ms=max(0, recorded_at_ms - int(plan["planned_at_ms"])),
            last_refresh_status="partial" if kind in partial_kinds else "success",
            last_corpus_hash=str(plan.get("corpus_hash") or "") or None,
            entity_count=counts[kind],
            coverage_pct=None,
            smoke_hit_rate=None,
            embedding_cost_usd_30d=0.0,
            partial_sources_30d=partial_sources,
        )
        for kind in sorted(planned_kinds)
    )


def run_vs_upsert(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T10 — upsert embeddings into Mosaic AI Vector Search.

    Builds the per-content-hash entity_metadata map by walking
    graph_builder.json's extensions + docs/blog chunks, then invokes
    :func:`vs_upsert.vs_upsert_embeddings` against the production VS
    client.
    """

    from brickvision_runtime.capability_graph import vs_upsert
    from brickvision_runtime.capability_graph.schemas.types import EmbeddingCacheRow

    started_at_ms = _now_ms()
    vs_endpoint = _read_env(
        "BV_INDEXER_VS_ENDPOINT", default=_DEFAULT_VS_ENDPOINT,
    )
    vs_index_short = _read_env(
        "BV_INDEXER_VS_INDEX_NAME", default=_DEFAULT_VS_INDEX_NAME,
    )
    index_name = f"{catalog}.{_resolve_schema()}.{vs_index_short}"

    embed_artifact = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_EMBED),
    )
    embeddings = tuple(
        _hydrate(EmbeddingCacheRow, r) for r in embed_artifact["rows"]
    )

    graph = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_GRAPH_BUILDER),
    )
    snapshot_id = graph["snapshot_id"]

    entity_metadata: dict[str, dict[str, Any]] = {}
    for ext in graph["extensions"]:
        chunk_text = f"{ext['title']}\n\n{ext['synopsis']}\n\n{ext['when_to_use']}"
        entity_metadata[ext["last_indexed_corpus_hash"]] = {
            "entity_id": ext["extension_id"],
            "entity_kind": "extension",
            "meta_skill_id": ext["meta_skill_id"],
            "top_order_id": ext["top_order_id"],
            "chunk_text": chunk_text,
            "source_url": "",
        }

    plan = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
    )
    planned = set(plan.get("planned_sources", []))
    for docs_source, docs_key in (
        ("databricks-docs-aws", _TASK_KEY_DOCS_AWS),
        ("databricks-docs-azure", _TASK_KEY_DOCS_AZURE),
        ("databricks-docs-gcp", _TASK_KEY_DOCS_GCP),
    ):
        if docs_source in planned:
            for chunk in _read_json_artifact(
                path=_staging_path(catalog=catalog, run_id=run_id, task_key=docs_key),
            ).get("chunks", []):
                entity_metadata[chunk["content_hash"]] = {
                    "entity_id": chunk["chunk_id"],
                    "entity_kind": "docs_chunk",
                    "chunk_text": chunk.get("chunk_text", ""),
                    "source_url": chunk.get("page_id", ""),
                }
    if "databricks-blog" in planned:
        _blog_path_vs = _staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_BLOG)
        if Path(_blog_path_vs).exists():
            for chunk in _read_json_artifact(path=_blog_path_vs).get("chunks", []):
                entity_metadata[chunk["content_hash"]] = {
                    "entity_id": chunk["chunk_id"],
                    "entity_kind": "blog_chunk",
                    "chunk_text": chunk.get("chunk_text", ""),
                    "source_url": chunk.get("page_id", ""),
                }

    _ = vs_endpoint  # vs_upsert resolves the index handle internally
    result = vs_upsert.vs_upsert_embeddings(
        embeddings=embeddings, entity_metadata=entity_metadata,
        index_name=index_name,
        started_at_ms=started_at_ms, completed_at_ms=_now_ms(),
        sleep=time.sleep,
    )

    return {
        "rows_upserted": result.rows_upserted,
        "batches_attempted": result.batches_attempted,
        "batches_succeeded": result.batches_succeeded,
        "retries": result.retries,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "duration_ms": result.duration_ms,
        "_started_at_ms": started_at_ms,
    }


def run_smoke(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T11 — run the locked baseline against the just-built snapshot."""

    from brickvision_runtime.capability_graph import smoke
    from brickvision_runtime.capability_graph.schemas.types import SmokeBaselineRow

    started_at_ms = _now_ms()
    plan = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
    )
    snapshot_id = plan["snapshot_id"]
    vs_index_short = _read_env(
        "BV_INDEXER_VS_INDEX_NAME", default=_DEFAULT_VS_INDEX_NAME,
    )
    index_name = f"{catalog}.{_resolve_schema()}.{vs_index_short}"

    from pyspark.sql import SparkSession  # type: ignore[import-not-found]
    spark = SparkSession.builder.getOrCreate()
    df = spark.sql(
        f"SELECT * FROM {catalog}.{_resolve_schema()}.smoke_baseline"
    )
    baseline = tuple(
        SmokeBaselineRow(
            query_id=r["query_id"],
            query_text=r["query_text"],
            expected_top_1_extension_id=r["expected_top_1_extension_id"],
            baseline_hit_rate=float(r["baseline_hit_rate"]),
            locked_at_ms=int(r["locked_at_ms"]),
            locked_at_corpus_hash=r["locked_at_corpus_hash"],
        )
        for r in df.collect()
    )

    if not baseline:
        # First run — no smoke baseline populated yet. Allow promotion
        # so the index gets bootstrapped. Subsequent runs will have a
        # locked baseline and enforce the quality gate.
        return {
            "snapshot_id": snapshot_id,
            "queries_run": 0,
            "hits": 0, "misses": 0,
            "observed_hit_rate": 1.0,
            "baseline_hit_rate": 0.0,
            "passed": True,
            "per_query": [],
            "errors": [],
            "duration_ms": 0,
            "_started_at_ms": started_at_ms,
            "_bootstrap_mode": True,
        }

    result = smoke.run_smoke(
        baseline=baseline, snapshot_id=snapshot_id,
        index_name=index_name, started_at_ms=started_at_ms,
        completed_at_ms=_now_ms(),
    )

    return {
        "snapshot_id": result.snapshot_id,
        "queries_run": result.queries_run,
        "hits": result.hits, "misses": result.misses,
        "observed_hit_rate": result.observed_hit_rate,
        "baseline_hit_rate": result.baseline_hit_rate,
        "passed": result.passed,
        "per_query": [dataclasses.asdict(q) for q in result.per_query],
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "duration_ms": result.duration_ms,
        "_started_at_ms": started_at_ms,
    }


def run_promote(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T12 — atomically promote the snapshot if all gates pass.

    The gate sums up the 3 upstream JSONs (persist + vs_upsert + smoke)
    via :func:`promote.promote_snapshot`'s built-in gate evaluator;
    this task just hydrates the typed results and dispatches.
    """

    from brickvision_runtime.capability_graph import (
        persist, promote, smoke, vs_upsert,
    )

    started_at_ms = _now_ms()
    plan = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
    )
    snapshot_id = plan["snapshot_id"]

    persist_result = _hydrate(
        persist.PersistResult,
        _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PERSIST),
        ),
    )
    vs_result = _hydrate(
        vs_upsert.VsUpsertResult,
        _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_VS_UPSERT),
        ),
    )
    smoke_result = _hydrate(
        smoke.SmokeResult,
        _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_SMOKE),
        ),
    )

    # Compute the corpus-signature hash from graph_builder's content
    # hashes (deterministic + reproducible per §23.4.2).
    import hashlib
    graph = _read_json_artifact(
        path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_GRAPH_BUILDER),
    )
    sig_hasher = hashlib.sha256()
    for ext in sorted(graph["extensions"], key=lambda x: x["extension_id"]):
        sig_hasher.update(ext["last_indexed_corpus_hash"].encode("utf-8"))
    signature = sig_hasher.hexdigest()

    promoted_by = _read_env("BV_INDEXER_SP_NAME", default="bv_indexer_sp")
    result = promote.promote_snapshot(
        snapshot_id=snapshot_id,
        persist_result=persist_result,
        vs_upsert_result=vs_result,
        smoke_result=smoke_result,
        promoted_by=promoted_by, signature=signature,
        promoted_at_ms=started_at_ms, completed_at_ms=_now_ms(),
        catalog=catalog,
    )

    return {
        "snapshot_id": result.snapshot_id,
        "promoted": result.promoted,
        "promoted_at_ms": result.promoted_at_ms,
        "promoted_by": result.promoted_by,
        "signature": result.signature,
        "failed_gates": [dataclasses.asdict(g) for g in result.failed_gates],
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "duration_ms": result.duration_ms,
        "_started_at_ms": started_at_ms,
    }


def run_retention(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T13 — 30-day GC sweep."""

    from brickvision_runtime.capability_graph import retention

    started_at_ms = _now_ms()
    retention_days = _read_env_int(
        "BV_INDEXER_RETENTION_DAYS", default=30,
    )
    embedding_ttl_days = _read_env_int(
        "BV_INDEXER_EMBEDDING_TTL_DAYS", default=30,
    )

    from pyspark.sql import SparkSession  # type: ignore[import-not-found]
    spark = SparkSession.builder.getOrCreate()
    df = spark.sql(
        f"SELECT snapshot_id FROM {catalog}.{_resolve_schema()}."
        "active_snapshot_id WHERE singleton_key = 'singleton'"
    )
    rows = df.collect()
    if not rows:
        # No active snapshot yet (first-ever indexer run produced
        # a successful promotion, but the read happens before that
        # write committed?). Use the just-promoted snapshot id from
        # this run as the safe-to-keep marker.
        plan = _read_json_artifact(
            path=_staging_path(catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PLAN),
        )
        active_snapshot_id = plan["snapshot_id"]
    else:
        active_snapshot_id = rows[0]["snapshot_id"]

    result = retention.run_retention(
        now_ms=started_at_ms,
        active_snapshot_id=active_snapshot_id,
        catalog=catalog,
        retention_days=retention_days,
        embedding_ttl_days=embedding_ttl_days,
        started_at_ms=started_at_ms,
        completed_at_ms=_now_ms(),
    )

    return {
        "now_ms": result.now_ms,
        "active_snapshot_id": result.active_snapshot_id,
        "snapshots_deactivated": result.snapshots_deactivated,
        "snapshots_failed": result.snapshots_failed,
        "embedding_cache_rows_deleted": result.embedding_cache_rows_deleted,
        "embedding_cache_failed": result.embedding_cache_failed,
        "staging_directories_deleted": result.staging_directories_deleted,
        "staging_directories_failed": result.staging_directories_failed,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "duration_ms": result.duration_ms,
        "_started_at_ms": started_at_ms,
    }


def run_sync(*, catalog: str, run_id: str) -> Mapping[str, Any]:
    """Task T14 — sync the just-promoted snapshot to Lakebase.

    Reads the promote artifact (to confirm a successful promote
    happened — sync is a no-op when the gate failed), then calls
    :func:`publish.publish_to_lakebase` for the 10 UI-readable tables.

    Per the architectural decisions documented in
    :mod:`brickvision_runtime.capability_graph.publish`:

      * The synced tables live at ``BV_CATALOG.BV_SCHEMA.<table>_synced``
        — same UC catalog/schema as the source Delta tables. No
        separate Lakebase-typed UC catalog.
      * The Postgres-side schema auto-equals ``BV_SCHEMA``
        (UC schema name → Postgres schema name auto-mapping).
      * ``create_database_objects_if_missing=True`` means we don't
        run any psycopg DDL.

    Fatal end-to-end gate: per-table SDK failures or Lakebase sync lag
    raise from :func:`publish.publish_to_lakebase`, so the sync task
    fails instead of reporting a completed indexer run before Postgres
    has caught up to the promoted Delta snapshot.

    The whole task is gated by ``BV_LAKEBASE_PROJECT_ID``: when the
    env var is empty (Lakebase not yet provisioned in the workspace),
    sync becomes a structured no-op. This keeps the indexer Job
    bootable before Lakebase Autoscaling has been enabled.
    """

    from brickvision_runtime.capability_graph import publish

    started_at_ms = _now_ms()
    project_id = os.environ.get("BV_LAKEBASE_PROJECT_ID", "").strip()
    if not project_id:
        return {
            "snapshot_id": "",
            "skipped_reason": "BV_LAKEBASE_PROJECT_ID is empty",
            "tables_created": 0,
            "tables_refreshed": 0,
            "tables_failed": 0,
            "outcomes": [],
            "_started_at_ms": started_at_ms,
        }

    promote_artifact = _read_json_artifact(
        path=_staging_path(
            catalog=catalog, run_id=run_id, task_key=_TASK_KEY_PROMOTE,
        ),
    )
    if not promote_artifact.get("promoted"):
        return {
            "snapshot_id": promote_artifact.get("snapshot_id", ""),
            "skipped_reason": "promote gate did not pass",
            "tables_created": 0,
            "tables_refreshed": 0,
            "tables_failed": 0,
            "outcomes": [],
            "_started_at_ms": started_at_ms,
        }

    branch = (
        os.environ.get("BV_LAKEBASE_BRANCH", "production").strip()
        or "production"
    )
    postgres_db = (
        os.environ.get("BV_LAKEBASE_DATABASE", "databricks_postgres").strip()
        or "databricks_postgres"
    )
    sync_mode = (
        os.environ.get("BV_LAKEBASE_SYNC_MODE", "snapshot").strip().lower()
        or "snapshot"
    )
    dry_run = os.environ.get("BV_DRY_RUN", "false").strip().lower() == "true"

    result = publish.publish_to_lakebase(
        snapshot_id=promote_artifact["snapshot_id"],
        catalog=catalog,
        schema=_resolve_schema(),
        project_id=project_id,
        branch=branch,
        postgres_database=postgres_db,
        sync_mode=sync_mode,
        dry_run=dry_run,
        started_at_ms=started_at_ms,
        reset_existing=(
            os.environ.get("BV_LAKEBASE_SYNC_RESET_EXISTING", "true")
            .strip()
            .lower()
            in {"1", "true", "yes"}
        ),
    )

    return {
        "snapshot_id": result.snapshot_id,
        "branch_resource_path": result.branch_resource_path,
        "postgres_database": result.postgres_database,
        "sync_mode": result.sync_mode,
        "tables_created": result.tables_created,
        "tables_refreshed": result.tables_refreshed,
        "tables_failed": result.tables_failed,
        "skipped_dry_run": result.skipped_dry_run,
        "sync_verified": result.sync_verified,
        "sync_wait_ms": result.sync_wait_ms,
        "synced_snapshot_id": result.synced_snapshot_id,
        "sync_errors": list(result.sync_errors),
        "outcomes": [dataclasses.asdict(o) for o in result.outcomes],
        "duration_ms": result.duration_ms,
        "_started_at_ms": started_at_ms,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_TASK_DISPATCH: Mapping[str, Callable[..., Mapping[str, Any]]] = {
    _TASK_KEY_PLAN: run_plan,
    _TASK_KEY_SDK: run_sdk,
    _TASK_KEY_OPENAPI_AWS: run_openapi_aws,
    _TASK_KEY_OPENAPI_AZURE: run_openapi_azure,
    _TASK_KEY_OPENAPI_GCP: run_openapi_gcp,
    _TASK_KEY_DOCS_AWS: run_docs_aws,
    _TASK_KEY_DOCS_AZURE: run_docs_azure,
    _TASK_KEY_DOCS_GCP: run_docs_gcp,
    _TASK_KEY_BLOG: run_blog,
    _TASK_KEY_LABS: run_labs,
    _TASK_KEY_GRAPH_BUILDER: run_graph_builder,
    _TASK_KEY_EMBED: run_embed,
    _TASK_KEY_PERSIST: run_persist,
    _TASK_KEY_VS_UPSERT: run_vs_upsert,
    _TASK_KEY_SMOKE: run_smoke,
    _TASK_KEY_PROMOTE: run_promote,
    _TASK_KEY_RETENTION: run_retention,
    _TASK_KEY_SYNC: run_sync,
}


_TASK_REASON_CODES: Mapping[str, str] = {
    _TASK_KEY_PLAN: "CAPABILITY_GRAPH_PLAN_FAILED",
    _TASK_KEY_SDK: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_OPENAPI_AWS: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_OPENAPI_AZURE: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_OPENAPI_GCP: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_DOCS_AWS: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_DOCS_AZURE: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_DOCS_GCP: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_BLOG: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_LABS: "CAPABILITY_GRAPH_SOURCE_PARSE_FAILED",
    _TASK_KEY_GRAPH_BUILDER: "CAPABILITY_GRAPH_BUILD_FAILED",
    _TASK_KEY_EMBED: "CAPABILITY_GRAPH_EMBEDDING_ENDPOINT_ERROR",
    _TASK_KEY_PERSIST: "CAPABILITY_GRAPH_PERSIST_WRITE_FAILED",
    _TASK_KEY_VS_UPSERT: "CAPABILITY_GRAPH_VS_UPSERT_FAILED",
    _TASK_KEY_SMOKE: "CAPABILITY_GRAPH_SMOKE_FAILED",
    _TASK_KEY_PROMOTE: "CAPABILITY_GRAPH_PROMOTE_GATE_FAILED",
    _TASK_KEY_RETENTION: "CAPABILITY_GRAPH_RETENTION_DEACTIVATE_FAILED",
    _TASK_KEY_SYNC: "CAPABILITY_GRAPH_PUBLISH_FAILED",
}


def _apply_env_overrides(pairs: Sequence[str]) -> None:
    """Apply repeatable ``--env KEY=VALUE`` overrides to ``os.environ``.

    Each ``pair`` is ``"BV_KEY=value"``; whitespace around the
    delimiter is trimmed. Empty values (``"BV_KEY="``) are ignored —
    DAB's ``{{job.parameters.<name>}}`` substitution emits an empty
    string when a job parameter has no default + no runtime override,
    and we don't want an empty env var to mask a hard-coded default
    that the task function would otherwise fall back to.

    Pre-existing ``os.environ`` values are overwritten (the Job's
    parameters carry the partner's deploy-time configuration; they
    take precedence over whatever the runtime image happened to set).
    """

    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(
                f"--env expects KEY=VALUE, got {pair!r} "
                "(no '=' delimiter)",
            )
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise SystemExit(
                f"--env got empty KEY in {pair!r}",
            )
        if not value:
            # Empty value — likely an unset DAB job parameter; skip
            # so the task function's default-resolution path runs.
            continue
        os.environ[key] = value


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch entry point.

    Invoked by the DAB as::

        spark_python_task:
          python_file: src/brickvision_runtime/databricks_jobs/run_capability_indexer.py
          parameters:
            - "--task"
            - "sdk"
            - "--run-id"
            - "{{job.run_id}}"
            - "--catalog"
            - "{{job.parameters.catalog}}"
            - "--env"
            - "BV_SCHEMA={{job.parameters.schema}}"
            - "--env"
            - "BV_INDEXER_WAREHOUSE_ID={{job.parameters.warehouse_id}}"
            # ... etc; one --env pair per BV_* var the task may need.

    The ``--env`` flag is repeatable and each ``KEY=VALUE`` is
    applied to ``os.environ`` BEFORE the task function runs (see
    :func:`_apply_env_overrides`). This is how serverless tasks
    receive ``BV_*`` configuration without per-task
    ``environment_variables`` support in DAB.
    """

    parser = argparse.ArgumentParser(
        description="BrickVision Capability Graph indexer (multi-task Job)",
    )
    parser.add_argument(
        "--task", required=True, choices=_ALL_TASK_KEYS,
        help="Which DAG task to execute on this invocation.",
    )
    parser.add_argument(
        "--run-id", required=True,
        help="Job run id (typically {{job.run_id}}); becomes part of "
             "the snapshot_id and the staging-path prefix.",
    )
    parser.add_argument(
        "--catalog", default=None,
        help="UC catalog (defaults to BV_CATALOG env or 'brickvision').",
    )
    parser.add_argument(
        "--env", action="append", default=[], metavar="KEY=VALUE",
        help="Repeatable env-var override applied to os.environ "
             "before dispatch. Used by the DAB to forward BV_* "
             "configuration into the serverless task process.",
    )
    args = parser.parse_args(argv)

    _apply_env_overrides(args.env or ())

    catalog = args.catalog or _read_env("BV_CATALOG", default=_DEFAULT_CATALOG)
    task_key: str = args.task
    run_id: str = args.run_id
    task_fn = _TASK_DISPATCH[task_key]

    started_at_ms = _now_ms()
    try:
        payload = task_fn(catalog=catalog, run_id=run_id)
    except BaseException as exc:  # noqa: BLE001 — top-level catcher
        return _emit_failure(
            catalog=catalog, run_id=run_id, task_key=task_key,
            started_at_ms=started_at_ms, exc=exc,
            reason_code=_TASK_REASON_CODES[task_key],
        )

    started_override = payload.get("_started_at_ms")
    if isinstance(started_override, int):
        started_at_ms = started_override

    payload_clean = {k: v for k, v in payload.items() if not k.startswith("_")}

    return _emit_success(
        catalog=catalog, run_id=run_id, task_key=task_key,
        started_at_ms=started_at_ms, payload=payload_clean,
    )


if __name__ == "__main__":  # pragma: no cover — runtime entry point
    _rc = main()
    # In Databricks exec context, SystemExit(0) is treated as a failure.
    # Only exit with non-zero codes; let success fall through silently.
    if _rc:
        sys.exit(_rc)


__all__ = [
    "main",
    "run_plan",
    "run_sdk",
    "run_openapi_aws",
    "run_openapi_azure",
    "run_openapi_gcp",
    "run_docs_aws",
    "run_docs_azure",
    "run_docs_gcp",
    "run_blog",
    "run_labs",
    "run_graph_builder",
    "run_embed",
    "run_persist",
    "run_vs_upsert",
    "run_smoke",
    "run_promote",
    "run_retention",
    "run_sync",
]
