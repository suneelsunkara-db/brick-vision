"""N13: `.env` loader respecting `BV_MODE`.

Per `docs/19-local-development.md` §15: precedence is `.env` <
environment variables < DAB variables (deployed). The loader REFUSES to
start with `BV_MODE=prod` if a `.env` file is present (production must
read from environment / DAB exclusively).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class EnvLoadError(RuntimeError):
    """`.env` loader refused to start (e.g. prod mode + .env present)."""


def assert_mlflow_version() -> None:
    """B9 closure: triple-defence MLflow 3.x version check.

    Per `docs/10-generation-philosophy.md` §8.4.1, every install entry point
    asserts the MLflow major version is in the supported range.
    """
    try:
        import mlflow  # type: ignore[import-not-found]
    except ImportError:
        return
    major = int(mlflow.__version__.split(".")[0])
    if major < 3:
        raise EnvLoadError(
            f"MLFLOW_VERSION_BELOW_FLOOR: mlflow {mlflow.__version__} < 3.0",
        )


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Parse a `.env` file (KEY=VALUE per line; `#` comments). Returns dict;
    does NOT mutate `os.environ` — caller decides precedence.
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]):
            value = value[1:-1]
        out[key] = value
    return out


def resolve_env(*, dotenv_path: str | Path = ".env") -> dict[str, str]:
    """Apply BrickVision precedence: .env < os.environ.

    Raises `EnvLoadError` if `BV_MODE=prod` and a `.env` file is present.
    """
    dotenv = load_dotenv(dotenv_path)
    bv_mode = os.environ.get("BV_MODE", dotenv.get("BV_MODE", "local"))
    if bv_mode == "prod" and Path(dotenv_path).exists():
        raise EnvLoadError(
            "PROD_MODE_DOES_NOT_PERMIT_DOTENV: BV_MODE=prod but .env file present",
        )
    merged = dict(dotenv)
    merged.update({k: v for k, v in os.environ.items() if k.startswith(("BV_", "DATABRICKS_"))})
    merged["BV_MODE"] = bv_mode
    return merged


def get_str(env: dict[str, str], key: str, default: str | None = None) -> str:
    val = env.get(key, default)
    if val is None:
        raise EnvLoadError(f"required env var missing: {key}")
    return val


def get_int(env: dict[str, str], key: str, default: int | None = None) -> int:
    val = env.get(key)
    if val is None:
        if default is None:
            raise EnvLoadError(f"required env var missing: {key}")
        return default
    return int(val)


def get_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    val = env.get(key)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def is_fake_llm(env: dict[str, str] | None = None) -> bool:
    env = env or resolve_env()
    return get_bool(env, "BV_FAKE_LLM", default=False)


def get_mode(env: dict[str, str] | None = None) -> str:
    env = env or resolve_env()
    return env.get("BV_MODE", "local")


__all__ = [
    "EnvLoadError",
    "assert_mlflow_version",
    "get_bool",
    "get_int",
    "get_mode",
    "get_str",
    "is_fake_llm",
    "load_dotenv",
    "resolve_env",
]
