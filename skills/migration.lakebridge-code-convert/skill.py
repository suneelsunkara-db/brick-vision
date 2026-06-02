"""Mechanical skill: ``skill:migration.lakebridge-code-convert``."""

from __future__ import annotations

from importlib import metadata, util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:migration.lakebridge-code-convert",
    version="0.1.0",
    dag=DAG(name="migration.lakebridge-code-convert"),
    constitutional=(
        "code.conversion.must.not.fabricate.outputs",
        "switch.orchestration.must.be.separate.from.sql.transpile",
        "blocked.state.must.explain.missing.bindings",
    ),
)


def run_migration_lakebridge_code_convert(
    *,
    source_path: str | None = None,
    output_path: str | None = None,
    source_technology: str | None = "pyspark",
    target_technology: str | None = "databricks",
    model_endpoint: str | None = None,
    workspace_output_folder: str | None = None,
    switch_config_path: str | None = None,
    workspace_host: str | None = None,
    workspace_token_present: bool = False,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """Run Lakebridge Switch when ready, otherwise report PySpark conversion blockers."""

    package = _switch_package()
    cli = _databricks_cli()
    lakebridge = _lakebridge_switch_cli()
    checks = [
        _check(
            "switch_package",
            bool(package["available"] or lakebridge["available"]),
            "Lakebridge Switch is available",
            "Install Lakebridge Switch before running PySpark conversion.",
            {"python_package": package, "lakebridge_cli": lakebridge},
        ),
        _check(
            "databricks_cli",
            cli["available"],
            "Databricks CLI is available",
            "Install/configure the Databricks CLI before running Lakebridge Switch.",
            cli,
        ),
        _check(
            "source_path",
            _is_volume_path(source_path),
            "Legacy PySpark source path is bound to a UC Volume",
            "Bind source_path to /Volumes/<catalog>/<schema>/<volume>/... containing legacy PySpark code.",
            {"value": source_path or ""},
        ),
        _check(
            "output_path",
            _is_volume_path(output_path),
            "Converted PySpark output path is bound to a UC Volume",
            "Bind output_path to /Volumes/<catalog>/<schema>/<volume>/... for converted PySpark artifacts.",
            {"value": output_path or ""},
        ),
        _check(
            "workspace_credentials",
            bool(workspace_host and workspace_token_present),
            "Databricks workspace host/token are available",
            "Set DATABRICKS_HOST and DATABRICKS_TOKEN for Switch orchestration.",
            {"workspace_host": workspace_host or "", "token_present": workspace_token_present},
        ),
        _check(
            "model_endpoint",
            bool(model_endpoint),
            "Foundation model endpoint is configured",
            "Set BV_SWITCH_MODEL_ENDPOINT or bind model_endpoint.",
            {"value": model_endpoint or ""},
        ),
        _check(
            "workspace_output_folder",
            _is_workspace_path(workspace_output_folder),
            "Switch Workspace output folder is bound",
            "Bind workspace_output_folder to /Workspace/... for the Lakebridge Switch command.",
            {"value": workspace_output_folder or ""},
        ),
    ]
    missing = [item["check_id"] for item in checks if item["status"] != "passed"]
    command = _switch_command(
        source_path=source_path,
        output_path=output_path,
        source_technology=source_technology or "pyspark",
        model_endpoint=model_endpoint,
        workspace_output_folder=workspace_output_folder,
    )
    if missing:
        return _blocked_result(
            source_path=source_path,
            output_path=output_path,
            source_technology=source_technology,
            target_technology=target_technology,
            model_endpoint=model_endpoint,
            workspace_output_folder=workspace_output_folder,
            switch_config_path=switch_config_path,
            checks=checks,
            missing=missing,
            command=command,
        )

    run = _run_switch_command(
        command=command,
        source_path=source_path or "",
        output_path=output_path or "",
        source_technology=source_technology or "python",
        model_endpoint=model_endpoint or "",
        workspace_output_folder=workspace_output_folder or "",
        switch_config_path=switch_config_path,
        timeout_seconds=timeout_seconds,
    )
    status = "code_conversion_completed" if run["return_code"] == 0 else "code_conversion_failed"
    return {
        "status": status,
        "executed": run["return_code"] == 0,
        "proof_kind": "migration_code_convert_switch_cli",
        "skill_id": "skill:migration.lakebridge-code-convert",
        "code_convert_preflight": {
            "source_path": source_path or "",
            "output_path": output_path or "",
            "source_technology": source_technology or "python",
            "target_technology": target_technology or "databricks",
            "model_endpoint": model_endpoint or "",
            "workspace_output_folder": workspace_output_folder or "",
            "switch_config_path": switch_config_path or "",
            "checks": checks,
            "missing": [],
        },
        "switch_run": run,
        "message": (
            "Lakebridge Switch code conversion command completed."
            if run["return_code"] == 0
            else "Lakebridge Switch code conversion command failed; inspect switch_run output."
        ),
    }


def _blocked_result(
    *,
    source_path: str | None,
    output_path: str | None,
    source_technology: str | None,
    target_technology: str | None,
    model_endpoint: str | None,
    workspace_output_folder: str | None,
    switch_config_path: str | None,
    checks: list[dict[str, Any]],
    missing: list[str],
    command: list[str],
) -> dict[str, Any]:
    return {
        "status": "code_conversion_blocked",
        "executed": False,
        "proof_kind": "migration_code_convert_preflight",
        "skill_id": "skill:migration.lakebridge-code-convert",
        "code_convert_preflight": {
            "source_path": source_path or "",
            "output_path": output_path or "",
            "source_technology": source_technology or "python",
            "target_technology": target_technology or "databricks",
            "model_endpoint": model_endpoint or "",
            "workspace_output_folder": workspace_output_folder or "",
            "switch_config_path": switch_config_path or "",
            "checks": checks,
            "missing": missing,
        },
        "switch_command": command,
        "install_command": [
            "databricks",
            "labs",
            "lakebridge",
            "install-transpile",
            "--include-llm-transpiler",
            "true",
        ],
        "message": (
            "Code Convert is for legacy PySpark conversion and is separate from SQL transpilation. "
            "Switch orchestration is blocked until the preflight checks pass."
        ),
    }


def _databricks_cli() -> dict[str, Any]:
    path = shutil.which("databricks") or ""
    return {"available": bool(path), "path": path}


def _lakebridge_switch_cli() -> dict[str, Any]:
    if not shutil.which("databricks"):
        return {"available": False, "command": []}
    installed = subprocess.run(  # noqa: S603
        ["databricks", "labs", "installed"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_command_env(),
        cwd=tempfile.gettempdir(),
    )
    command = ["databricks", "labs", "lakebridge", "llm-transpile", "--help"]
    probe = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_command_env(),
        cwd=tempfile.gettempdir(),
    )
    installed_output = installed.stdout + installed.stderr
    help_output = probe.stdout + probe.stderr
    return {
        "available": (
            installed.returncode == 0
            and "lakebridge" in installed_output
            and probe.returncode == 0
            and "llm-transpile" in help_output
            and "databricks labs [command]" not in help_output
            and "unknown flag" not in help_output
        ),
        "command": command,
        "installed_return_code": installed.returncode,
        "installed_stdout": installed.stdout[-1000:],
        "installed_stderr": installed.stderr[-1000:],
        "return_code": probe.returncode,
        "stdout": probe.stdout[-1000:],
        "stderr": probe.stderr[-1000:],
    }


def _is_volume_path(value: str | None) -> bool:
    return bool(value and value.startswith("/Volumes/") and len(value.split("/")) >= 6)


def _is_workspace_path(value: str | None) -> bool:
    return bool(value and value.startswith("/Workspace/"))


def _switch_command(
    *,
    source_path: str | None,
    output_path: str | None,
    source_technology: str,
    model_endpoint: str | None,
    workspace_output_folder: str | None,
) -> list[str]:
    catalog, schema, volume = _volume_parts(output_path or source_path or "")
    command = [
        "databricks",
        "labs",
        "lakebridge",
        "llm-transpile",
        "--input-source",
        source_path or "",
        "--output-ws-folder",
        workspace_output_folder or "",
        "--source-dialect",
        source_technology or "pyspark",
        "--accept-terms",
        "true",
    ]
    if catalog:
        command.extend(["--catalog-name", catalog])
    if schema:
        command.extend(["--schema-name", schema])
    if volume:
        command.extend(["--volume", volume])
    if model_endpoint:
        command.extend(["--foundation-model", model_endpoint])
    return command


def _run_switch_command(
    *,
    command: list[str],
    source_path: str,
    output_path: str,
    source_technology: str,
    model_endpoint: str,
    workspace_output_folder: str,
    switch_config_path: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bv-switch-") as tmpdir:
        if switch_config_path:
            return _run_switch_job_with_config(
                source_path=source_path,
                output_path=output_path,
                source_technology=source_technology,
                model_endpoint=model_endpoint,
                workspace_output_folder=workspace_output_folder,
                switch_config_path=switch_config_path,
                stages=stages,
                timeout_seconds=timeout_seconds,
            )
        local_source = f"{tmpdir}/source"
        staged_command = list(command)
        if _is_volume_path(source_path):
            source_copy = _run_command(
                ["databricks", "fs", "cp", _dbfs_volume_path(source_path), local_source, "--recursive"],
                timeout_seconds=timeout_seconds,
            )
            stages.append({"stage": "copy_source_volume_to_local", **source_copy})
            staged_command = _replace_arg(staged_command, "--input-source", local_source)
            if source_copy["return_code"] != 0:
                return _switch_run_result(staged_command, source_copy, stages)

        workspace_output = _arg_value(staged_command, "--output-ws-folder")
        if workspace_output:
            workspace_mkdir = _run_command(
                ["databricks", "workspace", "mkdirs", workspace_output],
                timeout_seconds=timeout_seconds,
            )
            stages.append({"stage": "prepare_workspace_output", **workspace_mkdir})
            if workspace_mkdir["return_code"] != 0:
                return _switch_run_result(staged_command, workspace_mkdir, stages)

        started = _run_command(staged_command, timeout_seconds=timeout_seconds)
        stages.append({"stage": "run_switch_cli", **started})
        if started["return_code"] != 0:
            return _switch_run_result(staged_command, started, stages)

        run_id = _switch_run_id(started)
        if run_id:
            wait_run = _wait_for_job_run(run_id=run_id, timeout_seconds=timeout_seconds)
            stages.append({"stage": "wait_switch_job", **wait_run})
            if wait_run["return_code"] != 0:
                return _switch_run_result(staged_command, wait_run, stages)

        local_output = f"{tmpdir}/converted"
        export_run = _run_command(
            ["databricks", "workspace", "export-dir", workspace_output, local_output, "--overwrite"],
            timeout_seconds=timeout_seconds,
        )
        stages.append({"stage": "export_workspace_output", **export_run})
        if export_run["return_code"] != 0:
            return _switch_run_result(staged_command, export_run, stages)

        output_mkdir = _run_command(
            ["databricks", "fs", "mkdirs", _dbfs_volume_path(output_path)],
            timeout_seconds=timeout_seconds,
        )
        stages.append({"stage": "prepare_output_volume", **output_mkdir})
        output_copy = _run_command(
            ["databricks", "fs", "cp", local_output, _dbfs_volume_path(output_path), "--recursive", "--overwrite"],
            timeout_seconds=timeout_seconds,
        )
        stages.append({"stage": "copy_converted_output_to_volume", **output_copy})
        return _switch_run_result(staged_command, output_copy, stages)


def _run_command(command: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=_command_env(),
        cwd=tempfile.gettempdir(),
    )
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


def _run_switch_job_with_config(
    *,
    source_path: str,
    output_path: str,
    source_technology: str,
    model_endpoint: str,
    workspace_output_folder: str,
    switch_config_path: str,
    stages: list[dict[str, Any]],
    timeout_seconds: int,
) -> dict[str, Any]:
    catalog, schema, volume = _volume_parts(output_path or source_path)
    if not catalog or not schema or not volume:
        return _switch_run_result(
            [],
            {
                "return_code": 1,
                "stdout": "",
                "stderr": "Code Convert direct Switch job requires source/output paths under /Volumes/<catalog>/<schema>/<volume>.",
            },
            stages,
        )

    workspace_mkdir = _run_command(
        ["databricks", "workspace", "mkdirs", workspace_output_folder],
        timeout_seconds=120,
    )
    stages.append({"stage": "prepare_workspace_output", **workspace_mkdir})
    if workspace_mkdir["return_code"] != 0:
        return _switch_run_result([], workspace_mkdir, stages)

    job_id = _lakebridge_switch_job_id(timeout_seconds=timeout_seconds)
    job_params = {
        "input_dir": source_path,
        "output_dir": workspace_output_folder,
        "source_tech": source_technology,
        "catalog": catalog,
        "schema": schema,
        "foundation_model": model_endpoint,
        "switch_config_path": switch_config_path,
    }
    payload_file = Path(tempfile.gettempdir()) / f"bv-switch-code-run-{int(time.time())}.json"
    payload_file.write_text(json.dumps({"job_id": job_id, "job_parameters": job_params}), encoding="utf-8")
    run_now = _run_command(
        [
            "databricks",
            "jobs",
            "run-now",
            "--json",
            f"@{payload_file}",
            "--no-wait",
            "-o",
            "json",
        ],
        timeout_seconds=120,
    )
    stages.append(
        {
            "stage": "run_switch_job",
            **run_now,
            "job_id": job_id,
            "job_parameters": job_params,
        }
    )
    if run_now["return_code"] != 0:
        return _switch_run_result([], run_now, stages)

    run_id = _job_run_id(run_now)
    if not run_id:
        return _switch_run_result(
            [],
            {
                **run_now,
                "return_code": 1,
                "stderr": run_now.get("stderr") or run_now.get("stdout") or "Switch job did not return a run id.",
            },
            stages,
        )
    wait_run = _wait_for_job_run(run_id=run_id, timeout_seconds=timeout_seconds)
    stages.append({"stage": "wait_switch_job", **wait_run})
    if wait_run["return_code"] != 0:
        return _switch_run_result([], wait_run, stages)

    with tempfile.TemporaryDirectory(prefix="bv-switch-output-") as tmpdir:
        local_output = f"{tmpdir}/converted"
        export_run = _run_command(
            ["databricks", "workspace", "export-dir", workspace_output_folder, local_output, "--overwrite"],
            timeout_seconds=timeout_seconds,
        )
        stages.append({"stage": "export_workspace_output", **export_run})
        if export_run["return_code"] != 0:
            return _switch_run_result([], export_run, stages)
        output_preview = _converted_output_preview(Path(local_output))

        output_mkdir = _run_command(
            ["databricks", "fs", "mkdirs", _dbfs_volume_path(output_path)],
            timeout_seconds=timeout_seconds,
        )
        stages.append({"stage": "prepare_output_volume", **output_mkdir})
        if output_mkdir["return_code"] != 0:
            return _switch_run_result([], output_mkdir, stages)
        output_copy = _run_command(
            ["databricks", "fs", "cp", local_output, _dbfs_volume_path(output_path), "--recursive", "--overwrite"],
            timeout_seconds=timeout_seconds,
        )
        stages.append({"stage": "copy_converted_output_to_volume", **output_copy})
        return _switch_run_result(
            [
                "databricks",
                "jobs",
                "run-now",
                "--json",
                f"@{payload_file}",
                "--no-wait",
            ],
            output_copy,
            stages,
            output_preview,
        )


def _lakebridge_switch_job_id(*, timeout_seconds: int) -> int:
    listed = _run_command(
        ["databricks", "jobs", "list", "--name", "Lakebridge_Switch", "-o", "json"],
        timeout_seconds=min(timeout_seconds, 120),
    )
    if listed["return_code"] != 0:
        raise RuntimeError(listed.get("stderr") or listed.get("stdout") or "Could not list Lakebridge Switch job.")
    try:
        payload = json.loads(str(listed.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse Lakebridge Switch job list response.") from exc
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list) or not jobs:
        raise RuntimeError("Lakebridge_Switch job was not found. Run install-transpile with include LLM transpiler.")
    job_id = jobs[0].get("job_id") if isinstance(jobs[0], dict) else None
    if job_id is None:
        raise RuntimeError("Lakebridge_Switch job response did not include job_id.")
    return int(job_id)


def _job_run_id(run: dict[str, Any]) -> str:
    try:
        payload = json.loads(str(run.get("stdout") or ""))
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and payload.get("run_id"):
        return str(payload["run_id"])
    return _switch_run_id(run)


def _switch_run_id(run: dict[str, Any]) -> str:
    text = f"{run.get('stdout') or ''}\n{run.get('stderr') or ''}"
    match = re.search(r"/runs/(\d+)", text)
    return match.group(1) if match else ""


def _wait_for_job_run(*, run_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    command = ["databricks", "jobs", "get-run", run_id]
    last_payload: dict[str, Any] = {}
    last_stdout = ""
    last_stderr = ""
    while time.monotonic() < deadline:
        completed = _run_command(command, timeout_seconds=60)
        last_stdout = completed.get("stdout", "")
        last_stderr = completed.get("stderr", "")
        if completed["return_code"] != 0:
            return {**completed, "run_id": run_id}
        try:
            last_payload = json.loads(last_stdout)
        except json.JSONDecodeError:
            return {**completed, "return_code": 1, "run_id": run_id}
        state = (last_payload.get("state") or {}).get("life_cycle_state") or (
            last_payload.get("status") or {}
        ).get("state")
        result_state = (last_payload.get("state") or {}).get("result_state")
        if state in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            return {
                "command": command,
                "return_code": 0 if result_state in {"SUCCESS", None} and state == "TERMINATED" else 1,
                "stdout": json.dumps(
                    {
                        "run_id": run_id,
                        "state": state,
                        "result_state": result_state,
                        "run_page_url": last_payload.get("run_page_url"),
                    }
                ),
                "stderr": "" if result_state in {"SUCCESS", None} and state == "TERMINATED" else last_stderr,
                "run_id": run_id,
                "state": state,
                "result_state": result_state,
                "run_page_url": last_payload.get("run_page_url"),
            }
        time.sleep(10)
    return {
        "command": command,
        "return_code": 1,
        "stdout": last_stdout[-8000:],
        "stderr": f"Timed out waiting for Switch job run {run_id}. {last_stderr[-4000:]}",
        "run_id": run_id,
        "last_state": (last_payload.get("state") or {}).get("life_cycle_state"),
        "run_page_url": last_payload.get("run_page_url"),
    }


def _switch_run_result(
    command: list[str],
    completed: dict[str, Any],
    stages: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "command": command,
        "return_code": int(completed.get("return_code") or 0),
        "stdout": str(completed.get("stdout") or "")[-8000:],
        "stderr": str(completed.get("stderr") or "")[-8000:],
        "stages": stages,
    }
    if extra:
        result.update(extra)
    return result


def _converted_output_preview(local_output: Path) -> dict[str, Any]:
    files = [path for path in local_output.rglob("*") if path.is_file()]
    generated_files = [str(path.relative_to(local_output)) for path in files]
    preview_path = next((path for path in files if path.suffix in {".py", ".sql", ".scala", ".r"}), None)
    if preview_path is None and files:
        preview_path = files[0]
    preview = ""
    if preview_path is not None:
        try:
            preview = preview_path.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            preview = ""
    return {
        "generated_files": generated_files,
        "converted_artifact_name": str(preview_path.relative_to(local_output)) if preview_path else "",
        "converted_artifact_preview": preview,
    }


def _replace_arg(command: list[str], flag: str, value: str) -> list[str]:
    updated = list(command)
    if flag in updated:
        index = updated.index(flag)
        if index + 1 < len(updated):
            updated[index + 1] = value
    return updated


def _arg_value(command: list[str], flag: str) -> str:
    if flag not in command:
        return ""
    index = command.index(flag)
    return command[index + 1] if index + 1 < len(command) else ""


def _volume_parts(path: str) -> tuple[str, str, str]:
    if not _is_volume_path(path):
        return "", "", ""
    parts = path.split("/")
    return parts[2], parts[3], parts[4]


def _dbfs_volume_path(path: str) -> str:
    return "dbfs:" + path if path.startswith("/Volumes/") else path


def _command_env() -> dict[str, str]:
    env = dict(os.environ)
    env_file = _repo_env_file()
    if env_file is not None:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.split(" #", 1)[0].strip().strip("'\"")
            if key and value:
                env[key] = value
    env.setdefault("PIP_INDEX_URL", "https://pypi-proxy.cloud.databricks.com/simple")
    env.setdefault("UV_INDEX_URL", env["PIP_INDEX_URL"])
    return env


def _repo_env_file() -> Path | None:
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def _switch_package() -> dict[str, Any]:
    try:
        module_available = util.find_spec("databricks.labs.switch") is not None
    except ModuleNotFoundError:
        module_available = False
    version = ""
    for distribution_name in ("databricks-switch-plugin", "databricks-labs-switch"):
        try:
            version = metadata.version(distribution_name)
            module_available = True
            break
        except metadata.PackageNotFoundError:
            continue
    return {
        "available": module_available,
        "module": "databricks.labs.switch",
        "version": version,
    }


def _check(
    check_id: str,
    passed: bool,
    passed_message: str,
    blocked_message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "passed" if passed else "blocked",
        "message": passed_message if passed else blocked_message,
        "evidence": evidence,
    }


__all__ = ["SKILL", "run_migration_lakebridge_code_convert"]
