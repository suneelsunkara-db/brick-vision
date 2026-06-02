"""Shared helpers for the ``scripts/local_deploy/*`` orchestrator.

This module is **import-cheap by design** — it must work on a bare
Python 3.11+ interpreter without `databricks-sdk` installed (the
provisioner lazy-imports the SDK inside function bodies so a missing
dependency surfaces a clear error instead of an opaque `ImportError`
at module-load time).

The helpers are not part of the BrickVision runtime substrate (no
imports from ``brickvision_runtime``); they exist only to drive a
one-shot workspace bootstrap and never run inside the indexer itself.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import textwrap
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


_PHASE_WIDTH = 8
_LOG_FILE: Path | None = None


def configure_log_file(path: str | os.PathLike[str]) -> None:
    """Mirror every emitted line to ``path`` (append-only) so the
    re-runnable bash script + Python helpers share an audit trail."""

    global _LOG_FILE
    _LOG_FILE = Path(path)
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


_Level = Literal["info", "warn", "ok", "step", "fail"]
_PREFIX = {
    "info": " ·  ",
    "warn": " ⚠  ",
    "ok":   " ✓  ",
    "step": "──  ",
    "fail": " ✗  ",
}


def log(level: _Level, message: str, *, phase: str = "") -> None:
    """Single emit surface — formats to stderr + (optional) log file."""

    phase_tag = f"[{phase:<{_PHASE_WIDTH}}] " if phase else ""
    line = f"{_PREFIX[level]}{phase_tag}{message}"
    print(line, file=sys.stderr, flush=True)
    if _LOG_FILE is not None:
        with _LOG_FILE.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")


# ---------------------------------------------------------------------------
# .env loading (no external deps)
# ---------------------------------------------------------------------------


def load_dotenv(path: str | os.PathLike[str] = ".env") -> dict[str, str]:
    """Minimal ``.env`` loader — supports ``KEY=VALUE`` plus inline ``#``
    comments. Existing ``os.environ`` values **win** so a shell-exported
    override beats the file (matches python-dotenv's
    ``override=False`` default)."""

    target = Path(path)
    parsed: dict[str, str] = {}
    if not target.exists():
        return parsed
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        if "#" in value:
            value = value.split("#", 1)[0]
        value = value.strip().strip("'").strip('"')
        parsed[key] = value
        if key not in os.environ:
            os.environ[key] = value
    return parsed


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_required(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(
            textwrap.dedent(
                f"""
                ✗ Required env var {key!r} is empty.

                  Open .env in this repo, set the value, then re-run
                  `bash scripts/local_deploy.sh`. The .env.example file
                  documents every required key. Workspace auth in
                  particular needs a Databricks PAT or an OAuth
                  profile capable of CREATE CATALOG / SCIM SP create /
                  VS endpoint create.
                """
            ).strip()
        )
    return value


# ---------------------------------------------------------------------------
# Dataclasses for the typed config bag
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class LocalDeployConfig:
    """Typed view of all the env vars the provisioner reads.

    Every field that has a corresponding DAB variable in
    ``databricks.yml`` is forwarded as a ``--var KEY=VALUE`` flag at
    bundle-deploy time (see :func:`bundle_deploy_var_args`). This
    closes the gap where the provisioner would create catalog
    ``BV_CATALOG`` while the deployed Job ran with the DAB default
    ``brickvision_dev`` and produced empty Delta tables in the wrong
    catalog.
    """

    databricks_host: str
    databricks_token: str
    catalog: str
    schema: str
    vs_endpoint: str
    vs_index_name: str
    state_volume_name: str
    warehouse_id: str
    embedding_endpoint: str
    daily_token_cap: str
    freshness_tolerance_days: str
    bv_dry_run: str
    bv_fake_llm: str
    lakebase_project_id: str
    lakebase_branch: str
    lakebase_database: str
    lakebase_sync_mode: str
    indexer_sp_name: str
    app_sp_name: str
    ops_email: str
    auto_provision_sps: bool
    auto_provision_catalog: bool
    auto_provision_vs: bool
    auto_provision_warehouse: bool
    deploy_indexer_job: bool
    trigger_first_refresh: bool
    vs_endpoint_timeout_sec: int
    indexer_timeout_sec: int

    @classmethod
    def from_env(cls) -> "LocalDeployConfig":
        return cls(
            databricks_host=env_required("DATABRICKS_HOST"),
            databricks_token=env_required("DATABRICKS_TOKEN"),
            catalog=os.environ.get("BV_CATALOG", "brickvision").strip(),
            schema=os.environ.get("BV_SCHEMA", "brickvision").strip(),
            vs_endpoint=os.environ.get(
                "BV_VS_ENDPOINT", "brickvision-dev"
            ).strip(),
            vs_index_name=(
                os.environ.get("BV_INDEXER_VS_INDEX_NAME", "entity_index").strip()
                or "entity_index"
            ),
            state_volume_name=(
                os.environ.get("BV_INDEXER_STATE_VOLUME", "indexer-state").strip()
                or "indexer-state"
            ),
            warehouse_id=(
                os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
                or os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
                or os.environ.get("BV_WAREHOUSE_ID", "").strip()
            ),
            embedding_endpoint=(
                os.environ.get("LLM_EMBEDDING_TASKS", "").strip()
                or os.environ.get("BV_INDEXER_EMBEDDING_ENDPOINT", "").strip()
                or "databricks-qwen3-embedding-0-6b"
            ),
            daily_token_cap=(
                os.environ.get("BV_INDEXER_DAILY_TOKEN_CAP", "10000000").strip()
                or "10000000"
            ),
            freshness_tolerance_days=(
                os.environ.get("BV_INDEXER_FRESHNESS_TOLERANCE_DAYS", "2").strip()
                or "2"
            ),
            bv_dry_run=(
                os.environ.get("BV_DRY_RUN", "false").strip().lower() or "false"
            ),
            bv_fake_llm=(
                os.environ.get("BV_FAKE_LLM", "false").strip().lower() or "false"
            ),
            lakebase_project_id=os.environ.get(
                "BV_LAKEBASE_PROJECT_ID", ""
            ).strip(),
            lakebase_branch=(
                os.environ.get("BV_LAKEBASE_BRANCH", "production").strip()
                or "production"
            ),
            lakebase_database=(
                os.environ.get("BV_LAKEBASE_DATABASE", "databricks_postgres").strip()
                or "databricks_postgres"
            ),
            lakebase_sync_mode=(
                os.environ.get("BV_LAKEBASE_SYNC_MODE", "snapshot").strip().lower()
                or "snapshot"
            ),
            indexer_sp_name=os.environ.get(
                "BV_LOCAL_DEPLOY_INDEXER_SP_NAME", "bv_indexer_sp"
            ).strip(),
            app_sp_name=os.environ.get(
                "BV_LOCAL_DEPLOY_APP_SP_NAME", "bv_app_sp"
            ).strip(),
            ops_email=os.environ.get("BV_LOCAL_DEPLOY_OPS_EMAIL", "").strip(),
            auto_provision_sps=env_bool(
                "BV_LOCAL_DEPLOY_AUTO_PROVISION_SPS", True
            ),
            auto_provision_catalog=env_bool(
                "BV_LOCAL_DEPLOY_AUTO_PROVISION_CATALOG", True
            ),
            auto_provision_vs=env_bool(
                "BV_LOCAL_DEPLOY_AUTO_PROVISION_VS", True
            ),
            auto_provision_warehouse=env_bool(
                "BV_LOCAL_DEPLOY_AUTO_PROVISION_WAREHOUSE", True
            ),
            deploy_indexer_job=env_bool(
                "BV_LOCAL_DEPLOY_DEPLOY_INDEXER_JOB", True
            ),
            trigger_first_refresh=env_bool(
                "BV_LOCAL_DEPLOY_TRIGGER_FIRST_REFRESH", True
            ),
            vs_endpoint_timeout_sec=int(
                os.environ.get("BV_LOCAL_DEPLOY_VS_ENDPOINT_TIMEOUT_SEC", "900")
            ),
            indexer_timeout_sec=int(
                os.environ.get("BV_LOCAL_DEPLOY_INDEXER_TIMEOUT_SEC", "2400")
            ),
        )


def bundle_deploy_var_args(cfg: "LocalDeployConfig") -> list[str]:
    """Return the ``--var KEY=VALUE`` flags for ``databricks bundle deploy``.

    The DAB at ``databricks.yml`` declares variables (catalog, schema,
    warehouse, sync knobs) with conservative defaults that don't
    reflect the operator's ``.env``. Without forwarding these, the
    provisioned catalog and the deployed Job's runtime catalog would
    drift apart. This helper produces the shell-safe argv suffix
    aligned 1:1 with the ``variables:`` block in ``databricks.yml``.

    Empty / falsy values for required-but-empty knobs (e.g.
    ``warehouse_id`` when the operator hasn't supplied one yet) are
    skipped so the DAB default kicks in instead of an empty override.
    """

    args: list[str] = []

    def _add(name: str, value: str) -> None:
        if value:
            args.append(f"--var={name}={value}")

    _add("catalog", cfg.catalog)
    _add("schema", cfg.schema)
    _add("vs_endpoint", cfg.vs_endpoint)
    _add("indexer_vs_index_name", cfg.vs_index_name)
    _add("indexer_state_volume", cfg.state_volume_name)
    _add("indexer_warehouse_id", cfg.warehouse_id)
    _add("indexer_embedding_endpoint", cfg.embedding_endpoint)
    _add("indexer_daily_token_cap", cfg.daily_token_cap)
    _add("indexer_freshness_tolerance_days", cfg.freshness_tolerance_days)
    _add("indexer_sp", cfg.indexer_sp_name)
    if cfg.ops_email:
        _add("indexer_alert_email", cfg.ops_email)
    _add("bv_dry_run", cfg.bv_dry_run)
    _add("bv_fake_llm", cfg.bv_fake_llm)
    # Lakebase: only forward when the operator has set a project; an
    # empty project_id keeps the DAB default ("") which makes T14
    # publish a structured no-op (T01-T13 still produce Delta state).
    if cfg.lakebase_project_id:
        _add("lakebase_project_id", cfg.lakebase_project_id)
        _add("lakebase_branch", cfg.lakebase_branch)
        _add("lakebase_database", cfg.lakebase_database)
        _add("lakebase_sync_mode", cfg.lakebase_sync_mode)
    _add(
        "workspace_kg_cron",
        os.environ.get("BV_WORKSPACE_KG_SCHEDULE_CRON", "").strip(),
    )
    _add(
        "workspace_profile_id",
        os.environ.get("BV_ACTIVE_WORKSPACE_PROFILE", "").strip(),
    )
    _add("workspace_profile_config_dir", os.environ.get("BV_CONFIG_DIR", "").strip())
    _add(
        "workspace_kg_allowed_catalogs",
        os.environ.get("BV_WORKSPACE_KG_ALLOWED_CATALOGS", "").strip(),
    )
    _add(
        "workspace_kg_blocked_catalogs",
        os.environ.get("BV_WORKSPACE_KG_BLOCKED_CATALOGS", "").strip(),
    )
    _add(
        "workspace_kg_include_system",
        os.environ.get("BV_WORKSPACE_KG_INCLUDE_SYSTEM", "").strip(),
    )

    return args


# ---------------------------------------------------------------------------
# Lazy SDK client (single source of truth)
# ---------------------------------------------------------------------------


def workspace_client(cfg: LocalDeployConfig) -> Any:
    """Construct a single ``databricks.sdk.WorkspaceClient`` once.

    Caller-side lazy import keeps this module importable on a bare
    interpreter (test suite + the bash script's --doctor mode both
    rely on this property)."""

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        raise SystemExit(
            "✗ databricks-sdk is not installed. Install with:\n"
            "    pip install 'databricks-sdk>=0.68'\n"
            "  (or `uv pip install -e src/brickvision_runtime` to pull"
            " every BrickVision runtime dependency)."
        ) from exc

    return WorkspaceClient(
        host=cfg.databricks_host, token=cfg.databricks_token
    )


# ---------------------------------------------------------------------------
# Helpers shared across every phase
# ---------------------------------------------------------------------------


def poll_until(
    *,
    description: str,
    predicate: Any,
    timeout_sec: int,
    interval_sec: float = 5.0,
    phase: str = "",
) -> bool:
    """Block until ``predicate()`` returns truthy or ``timeout_sec``.

    Emits a heartbeat once every 30 s so the operator knows the script
    is alive during long-running cloud waits (VS endpoint cold-create,
    job runs)."""

    deadline = time.monotonic() + timeout_sec
    last_heartbeat = 0.0
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception as exc:  # noqa: BLE001 — broad on purpose
            log("warn", f"{description}: predicate raised {exc!r}", phase=phase)
        now = time.monotonic()
        if now - last_heartbeat > 30:
            remaining = max(0, int(deadline - now))
            log(
                "info",
                f"{description}: still waiting (≤ {remaining}s)",
                phase=phase,
            )
            last_heartbeat = now
        time.sleep(interval_sec)
    return False


def _exec_statement_attr(client: Any) -> Any:
    """Two SDK versions expose Statement Execution under different
    attribute names; canonicalise here."""

    return getattr(client, "statement_execution", None) or getattr(
        client, "statements"
    )


def execute_statement(
    client: Any,
    *,
    statement: str,
    warehouse_id: str,
    wait_timeout: str = "50s",
    catalog: str | None = None,
) -> Any:
    """Thin wrapper around ``statement_execution.execute_statement``
    for fire-and-forget DDL — most of our calls produce no rows so we
    only care about state == SUCCEEDED."""

    if not warehouse_id.strip():
        raise SystemExit(
            "✗ SQL warehouse id is required for Statement Execution. "
            "Set DATABRICKS_WAREHOUSE_ID or BV_INDEXER_WAREHOUSE_ID in .env.",
        )
    api = _exec_statement_attr(client)
    return api.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout=wait_timeout,
        catalog=catalog,
    )


def assert_succeeded(response: Any, *, statement_excerpt: str) -> None:
    """Raise SystemExit on any non-SUCCEEDED Statement Execution
    response, with the offending SQL excerpt for triage."""

    state = getattr(getattr(response, "status", None), "state", None)
    state_value = getattr(state, "value", state)
    if state_value not in {"SUCCEEDED", "succeeded"}:
        error = getattr(getattr(response, "status", None), "error", None)
        raise SystemExit(
            f"✗ Statement failed (state={state_value!r}): {error}\n"
            f"  SQL: {statement_excerpt[:200]}{'...' if len(statement_excerpt) > 200 else ''}"
        )


def chunk(seq: Iterable[Any], n: int) -> list[list[Any]]:
    """Split ``seq`` into chunks of size ``n`` for batched DDL
    statements (Statement Execution caps at ~16K characters per
    request on small warehouses)."""

    out: list[list[Any]] = []
    buf: list[Any] = []
    for item in seq:
        buf.append(item)
        if len(buf) >= n:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return out


__all__ = [
    "LocalDeployConfig",
    "assert_succeeded",
    "bundle_deploy_var_args",
    "chunk",
    "configure_log_file",
    "env_bool",
    "env_required",
    "execute_statement",
    "load_dotenv",
    "log",
    "poll_until",
    "workspace_client",
]
