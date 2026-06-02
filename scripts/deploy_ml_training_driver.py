"""Upload the BrickVision ML training driver to a Databricks Workspace path."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib import request


DEFAULT_WORKSPACE_PATH = "/Workspace/BrickVision/ml/training_driver.py"


def main() -> int:
    args = _parse_args()
    _load_local_env()
    source_path = args.source or _repo_root() / "src" / "brickvision_runtime" / "ml" / "training_driver.py"
    content = source_path.read_bytes()
    parent = str(Path(args.workspace_path).parent)
    _api_post("/api/2.0/workspace/mkdirs", {"path": parent})
    _api_post(
        "/api/2.0/workspace/import",
        {
            "path": args.workspace_path,
            "format": "SOURCE",
            "language": "PYTHON",
            "content": base64.b64encode(content).decode("ascii"),
            "overwrite": True,
        },
    )
    print(args.workspace_path)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-path", default=DEFAULT_WORKSPACE_PATH)
    parser.add_argument("--source", type=Path, default=None)
    return parser.parse_args()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_local_env() -> None:
    env_path = _repo_root() / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        if "#" in value:
            value = value.split("#", 1)[0]
        os.environ[key] = value.strip().strip("'").strip('"')


def _api_post(path: str, body: dict[str, object]) -> None:
    host = (os.environ.get("DATABRICKS_HOST") or "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN") or ""
    if not host or not token:
        raise SystemExit("DATABRICKS_HOST and DATABRICKS_TOKEN are required to upload the driver.")
    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        f"{host}{path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=60) as response:
        response.read()


if __name__ == "__main__":
    raise SystemExit(main())
