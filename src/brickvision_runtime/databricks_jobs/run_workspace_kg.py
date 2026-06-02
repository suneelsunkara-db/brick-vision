"""Databricks Job entry-point for Workspace KG refreshes.

The Workspace KG is calculated offline by a serverless Job and published
to Lakebase. UI/API paths should read the synced table, not perform live
workspace introspection.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


try:
    _SRC_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
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


_TASK_REFRESH_UC = "refresh_uc"
_TASK_PUBLISH = "publish"
_ALL_TASKS = (_TASK_REFRESH_UC, _TASK_PUBLISH)


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes"}


def _apply_env(entries: list[str]) -> None:
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"--env must be KEY=VALUE, got {entry!r}")
        key, value = entry.split("=", 1)
        os.environ[key] = value


def _write_result(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_profile(
    *,
    config_dir: str | None,
    profile_id: str | None,
) -> Any | None:  # noqa: ANN401
    if not config_dir and not profile_id:
        return None
    try:
        from brickvision_runtime.config import load_active_workspace_profile
    except ModuleNotFoundError as exc:
        if exc.name == "yaml":
            return None
        raise

    return load_active_workspace_profile(config_dir=config_dir, profile_id=profile_id)


def _run_refresh_uc(args: argparse.Namespace, started_at_ms: int) -> dict[str, Any]:
    from brickvision_runtime.skills.uc_catalog_introspect import run_uc_catalog_introspect

    profile = _load_profile(
        config_dir=args.config_dir or os.environ.get("BV_CONFIG_DIR"),
        profile_id=args.workspace_profile_id
        or os.environ.get("BV_ACTIVE_WORKSPACE_PROFILE"),
    )

    profile_id = args.workspace_profile_id or getattr(profile, "profile_id", None) or "default"
    workspace_id = args.workspace_id or getattr(profile, "workspace_id", None)
    include_system = _bool(args.include_system, default=bool(getattr(profile, "include_system", False)))
    allowed_catalogs = _split_csv(args.allowed_catalogs) or tuple(
        getattr(profile, "allowed_catalogs", ()) or (),
    )
    blocked_catalogs = _split_csv(args.blocked_catalogs) or tuple(
        getattr(profile, "blocked_catalogs", ()) or (),
    )
    config_hash = args.config_hash or getattr(profile, "config_hash", None)

    result = run_uc_catalog_introspect(
        workspace_profile_id=profile_id,
        workspace_id=workspace_id,
        include_system=include_system,
        catalog_filter=args.catalog_filter or None,
        allowed_catalogs=allowed_catalogs,
        blocked_catalogs=blocked_catalogs,
        config_hash=config_hash,
        run_id=args.run_id,
    )
    return {
        "task": _TASK_REFRESH_UC,
        "run_id": args.run_id,
        "workspace_profile_id": profile_id,
        "allowed_catalogs": list(allowed_catalogs),
        "blocked_catalogs": list(blocked_catalogs),
        "result": dataclasses.asdict(result),
        "duration_ms": max(0, int(time.time() * 1000) - started_at_ms),
    }


def _run_publish(args: argparse.Namespace, started_at_ms: int) -> dict[str, Any]:
    from brickvision_runtime.capability_graph.publish import (
        publish_workspace_kg_to_lakebase,
    )

    if not args.lakebase_project_id.strip():
        return {
            "task": _TASK_PUBLISH,
            "run_id": args.run_id,
            "action": "skipped",
            "reason": "BV_LAKEBASE_PROJECT_ID is empty",
            "duration_ms": max(0, int(time.time() * 1000) - started_at_ms),
        }

    result = publish_workspace_kg_to_lakebase(
        run_id=args.run_id,
        catalog=args.catalog,
        schema=args.schema,
        project_id=args.lakebase_project_id,
        branch=args.lakebase_branch,
        postgres_database=args.lakebase_database,
        sync_mode=args.lakebase_sync_mode,
        dry_run=_bool(os.environ.get("BV_DRY_RUN"), default=False),
        started_at_ms=started_at_ms,
    )
    return {
        "task": _TASK_PUBLISH,
        "run_id": args.run_id,
        "result": dataclasses.asdict(result),
        "duration_ms": max(0, int(time.time() * 1000) - started_at_ms),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Workspace KG job task.")
    parser.add_argument("--task", choices=_ALL_TASKS, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--catalog", default=os.environ.get("BV_CATALOG", "brickvision"))
    parser.add_argument("--schema", default=os.environ.get("BV_SCHEMA", "brickvision"))
    parser.add_argument("--workspace-profile-id", default="")
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--config-hash", default="")
    parser.add_argument("--include-system", default="")
    parser.add_argument("--catalog-filter", default="")
    parser.add_argument("--allowed-catalogs", default="")
    parser.add_argument("--blocked-catalogs", default="")
    parser.add_argument("--lakebase-project-id", default=os.environ.get("BV_LAKEBASE_PROJECT_ID", ""))
    parser.add_argument("--lakebase-branch", default=os.environ.get("BV_LAKEBASE_BRANCH", "production"))
    parser.add_argument("--lakebase-database", default=os.environ.get("BV_LAKEBASE_DATABASE", "databricks_postgres"))
    parser.add_argument("--lakebase-sync-mode", default=os.environ.get("BV_LAKEBASE_SYNC_MODE", "snapshot"))
    parser.add_argument("--result-path", default="")
    parser.add_argument("--env", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    started_at_ms = int(time.time() * 1000)
    try:
        _apply_env(args.env)
        if args.task == _TASK_REFRESH_UC:
            payload = _run_refresh_uc(args, started_at_ms)
        elif args.task == _TASK_PUBLISH:
            payload = _run_publish(args, started_at_ms)
        else:  # pragma: no cover - argparse enforces choices.
            raise ValueError(f"unknown task: {args.task}")
        _write_result(args.result_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 0
    except BaseException as exc:  # noqa: BLE001
        payload = {
            "task": args.task,
            "run_id": args.run_id,
            "error_kind": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
            "duration_ms": max(0, int(time.time() * 1000) - started_at_ms),
        }
        _write_result(args.result_path, payload)
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    _rc = main()
    # Databricks serverless wraps spark_python_task execution in IPython;
    # SystemExit(0) is reported as a workload failure there.
    if _rc:
        sys.exit(_rc)
