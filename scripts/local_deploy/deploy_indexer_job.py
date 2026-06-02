"""Deploy the capability indexer Job via Databricks Jobs API.

Uploads BrickVision workspace source files, translates the Job stanzas from
``databricks.yml`` into Jobs API payloads, then creates or resets the managed
serverless Jobs.

The job runs as the **current user** (the operator). No service principal
delegation, no extra entitlements, no workspace ACLs. For production
isolation a CI/CD pipeline can layer `run_as` on top; the local setup
path stays simple.

CLI::

    python3 scripts/local_deploy/deploy_indexer_job.py
    python3 scripts/local_deploy/deploy_indexer_job.py --dry-run

"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


from scripts.local_deploy._lib import (  # noqa: E402
    LocalDeployConfig,
    configure_log_file,
    load_dotenv,
    log,
)


_BUNDLE_FILE = _REPO_ROOT / "databricks.yml"
_API_PREVIEW_FILE = _REPO_ROOT / "databricks.local_deploy.job_api_preview.json"


# ---------------------------------------------------------------------------
# Variable rendering (replaces ${var.foo} placeholders from databricks.yml)
# ---------------------------------------------------------------------------


def _var_values(cfg: LocalDeployConfig) -> dict[str, str]:
    workspace_profile_id = os.environ.get(
        "BV_ACTIVE_WORKSPACE_PROFILE", "partner-dev",
    ).strip() or "partner-dev"
    workspace_config_dir = os.environ.get("BV_CONFIG_DIR", "config").strip() or "config"
    allowed_catalogs, blocked_catalogs, include_system = _workspace_scope_defaults(
        config_dir=workspace_config_dir,
        profile_id=workspace_profile_id,
    )
    return {
        "catalog": cfg.catalog,
        "schema": cfg.schema,
        "vs_endpoint": cfg.vs_endpoint,
        "indexer_vs_index_name": cfg.vs_index_name,
        "serverless_env_version": "2",
        "indexer_sp": "",
        "indexer_alert_email": cfg.ops_email or "brickvision-ops@example.com",
        "indexer_cron": os.environ.get("BV_INDEXER_SCHEDULE_CRON", "0 0 2 * * ?"),
        "indexer_warehouse_id": cfg.warehouse_id,
        "indexer_state_volume": cfg.state_volume_name,
        "indexer_embedding_endpoint": cfg.embedding_endpoint,
        "indexer_daily_token_cap": cfg.daily_token_cap,
        "indexer_freshness_tolerance_days": cfg.freshness_tolerance_days,
        "bv_dry_run": cfg.bv_dry_run,
        "bv_fake_llm": cfg.bv_fake_llm,
        "lakebase_project_id": cfg.lakebase_project_id,
        "lakebase_branch": cfg.lakebase_branch,
        "lakebase_database": cfg.lakebase_database,
        "lakebase_sync_mode": cfg.lakebase_sync_mode,
        "workspace_kg_cron": os.environ.get(
            "BV_WORKSPACE_KG_SCHEDULE_CRON", "0 0 3 * * ?",
        ),
        "workspace_profile_id": workspace_profile_id,
        "workspace_profile_config_dir": workspace_config_dir,
        "workspace_kg_allowed_catalogs": os.environ.get(
            "BV_WORKSPACE_KG_ALLOWED_CATALOGS", allowed_catalogs,
        ).strip(),
        "workspace_kg_blocked_catalogs": os.environ.get(
            "BV_WORKSPACE_KG_BLOCKED_CATALOGS", blocked_catalogs,
        ).strip(),
        "workspace_kg_include_system": os.environ.get(
            "BV_WORKSPACE_KG_INCLUDE_SYSTEM", include_system,
        ).strip().lower(),
        "evaluation_cron": os.environ.get(
            "BV_EVALUATION_SCHEDULE_CRON", "0 0 4 * * ?",
        ),
        "evaluation_warehouse_id": (
            os.environ.get("BV_EVALUATION_WAREHOUSE_ID", "").strip()
            or cfg.warehouse_id
        ),
        "mlflow_evaluation_experiment_id": os.environ.get(
            "BV_MLFLOW_EVALUATION_EXPERIMENT_ID", "",
        ).strip(),
    }


def _workspace_scope_defaults(
    *, config_dir: str, profile_id: str,
) -> tuple[str, str, str]:
    target = _REPO_ROOT / config_dir / "workspaces" / f"{profile_id}.workspace.yaml"
    if not target.exists():
        return "", "system", "false"
    try:
        import yaml  # noqa: PLC0415

        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return "", "system", "false"
    scope = payload.get("scope") if isinstance(payload, dict) else {}
    kg = payload.get("kg") if isinstance(payload, dict) else {}
    introspection = kg.get("introspection") if isinstance(kg, dict) else {}
    scope_map = scope if isinstance(scope, dict) else {}
    intro_map = introspection if isinstance(introspection, dict) else {}
    allowed = ",".join(str(v) for v in scope_map.get("allowed_catalogs", []) or [])
    blocked = ",".join(str(v) for v in scope_map.get("blocked_catalogs", []) or [])
    include_system = "true" if bool(intro_map.get("include_system", False)) else "false"
    return allowed, blocked or "system", include_system


def _render_vars(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        rendered = value
        for name, replacement in values.items():
            rendered = rendered.replace(f"${{var.{name}}}", replacement)
        return rendered
    if isinstance(value, list):
        return [_render_vars(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _render_vars(val, values) for key, val in value.items()}
    return value


# ---------------------------------------------------------------------------
# Workspace source upload
# ---------------------------------------------------------------------------


def _workspace_source_root(client: Any) -> str:
    user_name = None
    try:
        user_name = getattr(client.current_user.me(), "user_name", None)
    except Exception:  # noqa: BLE001
        pass
    if user_name:
        return f"/Workspace/Users/{user_name}/.brickvision/indexer"
    return "/Workspace/Shared/.brickvision/indexer"


def _upload_runtime_source(*, client: Any, workspace_root: str) -> None:
    import base64  # noqa: PLC0415

    from databricks.sdk.service.workspace import ImportFormat  # noqa: PLC0415

    source_root = _SRC / "brickvision_runtime"
    target_root = f"{workspace_root}/src/brickvision_runtime"
    client.workspace.mkdirs(target_root)

    uploaded = 0
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source_root)
        # Keep .py extension — AUTO format + .py creates a workspace FILE
        # (not a notebook). spark_python_task with source=WORKSPACE needs files.
        target = f"{target_root}/{relative.as_posix()}"
        client.workspace.mkdirs(str(Path(target).parent))
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        client.workspace.import_(
            path=target,
            content=content,
            format=ImportFormat.AUTO,
            overwrite=True,
        )
        uploaded += 1

    log("ok", f"uploaded {uploaded} runtime source files to {target_root}", phase="deploy")


def _upload_hand_authored_skills(*, client: Any, workspace_root: str) -> None:
    import base64  # noqa: PLC0415

    from databricks.sdk.service.workspace import ImportFormat  # noqa: PLC0415

    source_root = _REPO_ROOT / "skills"
    target_root = f"{workspace_root}/skills"
    client.workspace.mkdirs(target_root)

    uploaded = 0
    for path in sorted(source_root.rglob("*.yaml")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source_root)
        target = f"{target_root}/{relative.as_posix()}"
        client.workspace.mkdirs(str(Path(target).parent))
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        client.workspace.import_(
            path=target,
            content=content,
            format=ImportFormat.AUTO,
            overwrite=True,
        )
        uploaded += 1

    log("ok", f"uploaded {uploaded} skill manifest files to {target_root}", phase="deploy")


def _upload_evaluation_assets(*, client: Any, workspace_root: str) -> None:
    import base64  # noqa: PLC0415

    from databricks.sdk.service.workspace import ImportFormat  # noqa: PLC0415

    assets = [
        _REPO_ROOT / "scripts" / "run_evaluation_scorers.py",
        _REPO_ROOT / "scripts" / "sync_mlflow_eval_datasets.py",
    ]
    eval_root = _REPO_ROOT / "config" / "evaluation"
    assets.extend(
        path
        for path in sorted(eval_root.glob("*"))
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}
    )

    uploaded = 0
    for path in assets:
        relative = path.relative_to(_REPO_ROOT)
        target = f"{workspace_root}/{relative.as_posix()}"
        client.workspace.mkdirs(str(Path(target).parent))
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        client.workspace.import_(
            path=target,
            content=content,
            format=ImportFormat.AUTO,
            overwrite=True,
        )
        uploaded += 1

    log("ok", f"uploaded {uploaded} evaluation assets to {workspace_root}", phase="deploy")


# ---------------------------------------------------------------------------
# Jobs API payload construction
# ---------------------------------------------------------------------------


def _job_api_payload(
    bundle: dict[str, Any],
    *,
    job_key: str,
    cfg: LocalDeployConfig,
    workspace_root: str,
    run_as_user: str,
) -> dict[str, Any]:
    jobs = ((bundle.get("resources") or {}).get("jobs") or {})
    job = jobs.get(job_key)
    if not isinstance(job, dict):
        raise SystemExit(f"✗ resources.jobs.{job_key} missing from databricks.yml")

    payload = _render_vars(job, _var_values(cfg))
    payload = dict(payload)

    # Explicitly run as the current user — avoids the entire SP permission
    # chain (workspace-access entitlement, servicePrincipal.user role,
    # workspace object ACLs, UC grants to the SP).
    payload.pop("run_as", None)
    payload["run_as"] = {"user_name": run_as_user}

    payload["tags"] = {
        **dict(payload.get("tags") or {}),
        "brickvision": "true",
        "managed_by": "scripts/setup_data.sh",
    }

    for parameter in payload.get("parameters", []) or []:
        if not isinstance(parameter, dict):
            continue
        if parameter.get("name") == "manifest" and parameter.get("default") == "config/evaluation/evalsets.json":
            parameter["default"] = f"{workspace_root}/config/evaluation/evalsets.json"

    for task in payload.get("tasks", []) or []:
        spark_python = task.get("spark_python_task")
        if not isinstance(spark_python, dict):
            continue
        python_file = str(spark_python.get("python_file") or "")
        if python_file.startswith(("src/", "scripts/")):
            python_file = f"{workspace_root}/{python_file}"
        spark_python["python_file"] = python_file
        spark_python["source"] = "WORKSPACE"
        parameters = list(spark_python.get("parameters") or [])
        spark_python["parameters"] = [
            f"{workspace_root}/{item}" if item == "config/evaluation/evalsets.json" else item
            for item in parameters
        ]
        if task.get("task_key") == "graph_builder":
            parameters = list(spark_python.get("parameters") or [])
            parameters.extend([
                "--env",
                f"BV_INDEXER_SKILLS_DIR={workspace_root}/skills",
            ])
            spark_python["parameters"] = parameters
    return payload


def _find_job_id(client: Any, *, name: str) -> int | None:
    for job in client.jobs.list(name=name):
        settings = getattr(job, "settings", None)
        settings_name = getattr(settings, "name", None) if settings else None
        if settings_name == name or getattr(job, "job_id", None):
            job_id = getattr(job, "job_id", None)
            return int(job_id) if job_id is not None else None
    return None


# ---------------------------------------------------------------------------
# Main deploy logic
# ---------------------------------------------------------------------------


def _deploy(cfg: LocalDeployConfig, *, dry_run: bool) -> int:
    client: Any | None = None
    workspace_root = os.environ.get("BV_INDEXER_WORKSPACE_ROOT", "").strip()
    current_user = os.environ.get("DATABRICKS_USER", "").strip()
    if not dry_run:
        from scripts.local_deploy._lib import workspace_client  # noqa: PLC0415

        client = workspace_client(cfg)
        workspace_root = workspace_root or _workspace_source_root(client)
        current_user = getattr(client.current_user.me(), "user_name", None)
        if not current_user:
            raise SystemExit("✗ unable to resolve current Databricks user")
    else:
        workspace_root = workspace_root or "/Workspace/Shared/.brickvision/indexer"
        current_user = current_user or os.environ.get("USER", "dry-run-operator")
    log("info", f"job will run as: {current_user}", phase="deploy")

    bundle = _load_bundle_yaml()
    payloads = {
        key: _job_api_payload(
            bundle,
            job_key=key,
            cfg=cfg,
            workspace_root=workspace_root,
            run_as_user=current_user,
        )
        for key in ("capability_indexer", "workspace_kg_refresh", "evaluation_scorers")
    }

    _API_PREVIEW_FILE.write_text(
        json.dumps(payloads, indent=2, sort_keys=True), encoding="utf-8"
    )
    log("ok", f"wrote Jobs API preview → {_API_PREVIEW_FILE.relative_to(_REPO_ROOT)}", phase="deploy")

    if dry_run:
        log("ok", "dry-run complete — no source upload or Jobs API write", phase="deploy")
        return 0

    from databricks.sdk.service.jobs import JobSettings  # noqa: PLC0415

    if client is None:
        raise SystemExit("✗ Databricks client was not initialized")
    _upload_runtime_source(client=client, workspace_root=workspace_root)
    _upload_hand_authored_skills(client=client, workspace_root=workspace_root)
    _upload_evaluation_assets(client=client, workspace_root=workspace_root)

    for payload in payloads.values():
        existing_id = _find_job_id(client, name=str(payload["name"]))
        if existing_id is None:
            created = client.api_client.do("POST", "/api/2.1/jobs/create", body=payload)
            log("ok", f"created {payload['name']} (job_id={created.get('job_id')})", phase="deploy")
            continue

        client.jobs.reset(job_id=existing_id, new_settings=JobSettings.from_dict(payload))
        log("ok", f"reset {payload['name']} (job_id={existing_id})", phase="deploy")
    return 0


def _load_bundle_yaml() -> dict[str, Any]:
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:
        try:
            from brickvision_runtime._vendor.minyaml import safe_load  # noqa: PLC0415
        except ImportError:
            raise SystemExit(
                "✗ PyYAML not installed. Install with: pip install 'PyYAML>=6.0'"
            ) from exc

        return safe_load(_BUNDLE_FILE.read_text(encoding="utf-8")) or {}

    return yaml.safe_load(_BUNDLE_FILE.read_text(encoding="utf-8")) or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the Jobs API preview JSON and exit without deploying.",
    )
    args = parser.parse_args(argv)

    load_dotenv(_REPO_ROOT / ".env")
    log_path = os.environ.get("BV_LOCAL_DEPLOY_LOG_PATH", "./local_deploy.log")
    configure_log_file(log_path)

    cfg = LocalDeployConfig.from_env()
    log(
        "step",
        f"deploy_indexer_job.py — host={cfg.databricks_host} catalog={cfg.catalog}",
        phase="deploy",
    )

    if not cfg.deploy_indexer_job:
        log("info", "BV_LOCAL_DEPLOY_DEPLOY_INDEXER_JOB=false — skipping", phase="deploy")
        return 0

    if not _BUNDLE_FILE.exists():
        raise SystemExit(f"✗ databricks.yml not found at {_BUNDLE_FILE}")

    return _deploy(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
