"""N153 — three SDK / runtime floor pre-flights.

Per [`docs/19-local-development.md`](../../../../docs/19-local-development.md)
§15.5, the install must refuse to proceed if any of the three
runtime floors below are missed. The floors come straight from
upstream Databricks docs:

(a) ``databricks-sdk >= 0.68.0`` — required by the Lakehouse
    Monitoring Python API + UC Workspace Bindings APIs the
    install + observability + drift-watcher Jobs depend on.
(b) Python ``>= 3.10`` — required for Unity Catalog Functions used
    as agent tools (per the Databricks UC Functions doc).
(c) Every Job spec's ``environments[].spec.client == "2"`` — the
    Serverless environment version 2 (Ubuntu 22.04 + Python 3.11.10
    + Databricks Connect 15.4.5; release Nov 2024, end-of-support
    Nov 2027 per the Databricks Serverless environment versions doc).

All three are pure functions over typed inputs so unit tests can
parameterise them without touching the runtime; the CLI runners
are thin wrappers that call ``importlib.metadata`` /
``sys.version_info`` / a job-spec loader and return the same
``PreFlightFailure | None`` contract.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from brickvision.cli.install import PreFlightFailure
from brickvision_runtime.failures import ReasonCode

DATABRICKS_SDK_VERSION_FLOOR = (0, 68, 0)
PYTHON_VERSION_FLOOR = (3, 10)
SERVERLESS_ENV_VERSION_FLOOR = "2"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match((text or "").strip())
    if m is None:
        return None
    major, minor, patch = m.group(1), m.group(2), m.group(3)
    return (int(major), int(minor), int(patch or "0"))


# ---------------------------------------------------------------------------
# (a) databricks-sdk version floor
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DatabricksSdkProbe:
    """Observed databricks-sdk version (None ⇒ not importable)."""

    version: str | None


def check_databricks_sdk_version(
    *, probe: DatabricksSdkProbe
) -> PreFlightFailure | None:
    """Floor: ``databricks-sdk >= 0.68.0``."""

    if probe.version is None:
        return PreFlightFailure(
            reason_code=ReasonCode.DATABRICKS_SDK_VERSION_TOO_OLD,
            suggested_next_action=(
                f"pip install 'databricks-sdk>="
                f"{_floor_str(DATABRICKS_SDK_VERSION_FLOOR)}'"
            ),
            detail="databricks-sdk not importable",
        )
    parsed = _parse_version(probe.version)
    if parsed is None:
        return PreFlightFailure(
            reason_code=ReasonCode.DATABRICKS_SDK_VERSION_TOO_OLD,
            suggested_next_action=(
                f"pip install 'databricks-sdk>="
                f"{_floor_str(DATABRICKS_SDK_VERSION_FLOOR)}'"
            ),
            detail=f"unparseable version {probe.version!r}",
        )
    if parsed < DATABRICKS_SDK_VERSION_FLOOR:
        return PreFlightFailure(
            reason_code=ReasonCode.DATABRICKS_SDK_VERSION_TOO_OLD,
            suggested_next_action=(
                f"pip install 'databricks-sdk>="
                f"{_floor_str(DATABRICKS_SDK_VERSION_FLOOR)}'"
            ),
            detail=(
                f"observed databricks-sdk={probe.version}; floor"
                f" {_floor_str(DATABRICKS_SDK_VERSION_FLOOR)}"
            ),
        )
    return None


def probe_databricks_sdk_version() -> DatabricksSdkProbe:
    """Best-effort runtime probe (no top-level SDK import)."""

    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover — Python < 3.8 not supported
        return DatabricksSdkProbe(version=None)
    try:
        return DatabricksSdkProbe(version=version("databricks-sdk"))
    except PackageNotFoundError:
        return DatabricksSdkProbe(version=None)


# ---------------------------------------------------------------------------
# (b) Python interpreter version floor
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PythonVersionProbe:
    major: int
    minor: int
    patch: int


def check_python_version(*, probe: PythonVersionProbe) -> PreFlightFailure | None:
    """Floor: Python ``>= 3.10``."""

    if (probe.major, probe.minor) < PYTHON_VERSION_FLOOR:
        return PreFlightFailure(
            reason_code=ReasonCode.PYTHON_VERSION_TOO_OLD,
            suggested_next_action=(
                f"upgrade Python interpreter to >="
                f"{PYTHON_VERSION_FLOOR[0]}.{PYTHON_VERSION_FLOOR[1]}"
                " before re-running brickvision install"
            ),
            detail=(
                f"observed Python={probe.major}.{probe.minor}.{probe.patch};"
                f" floor"
                f" {PYTHON_VERSION_FLOOR[0]}.{PYTHON_VERSION_FLOOR[1]}"
            ),
        )
    return None


def probe_python_version() -> PythonVersionProbe:
    info = sys.version_info
    return PythonVersionProbe(major=info.major, minor=info.minor, patch=info.micro)


# ---------------------------------------------------------------------------
# (c) Serverless environment version floor (per Job spec)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class JobSpecProbe:
    """One Job spec the install plans to deploy."""

    name: str
    job_spec: Mapping[str, Any]


def check_serverless_env_version(
    *, probes: Iterable[JobSpecProbe]
) -> PreFlightFailure | None:
    """Floor: every ``environments[].spec.client == "2"``.

    Returns the first miss (typed) or ``None`` if all probes pass.
    """

    misses: list[str] = []
    for probe in probes:
        environments = probe.job_spec.get("environments")
        if not isinstance(environments, list) or not environments:
            misses.append(
                f"{probe.name}: missing environments[]"
            )
            continue
        for idx, env in enumerate(environments):
            if not isinstance(env, Mapping):
                misses.append(
                    f"{probe.name}.environments[{idx}]: not a mapping"
                )
                continue
            spec = env.get("spec")
            if not isinstance(spec, Mapping):
                misses.append(
                    f"{probe.name}.environments[{idx}].spec: missing"
                )
                continue
            client = str(spec.get("client", ""))
            if client != SERVERLESS_ENV_VERSION_FLOOR:
                misses.append(
                    f"{probe.name}.environments[{idx}].spec.client="
                    f"{client!r}; floor"
                    f" {SERVERLESS_ENV_VERSION_FLOOR!r}"
                )
    if misses:
        return PreFlightFailure(
            reason_code=ReasonCode.SERVERLESS_ENV_VERSION_INCOMPATIBLE,
            suggested_next_action=(
                f"set environments[].spec.client to"
                f" {SERVERLESS_ENV_VERSION_FLOOR!r} on the Job specs"
                " listed in detail"
            ),
            detail="; ".join(misses),
        )
    return None


def _floor_str(floor: tuple[int, int, int]) -> str:
    return f"{floor[0]}.{floor[1]}.{floor[2]}"


__all__ = [
    "DATABRICKS_SDK_VERSION_FLOOR",
    "PYTHON_VERSION_FLOOR",
    "SERVERLESS_ENV_VERSION_FLOOR",
    "DatabricksSdkProbe",
    "JobSpecProbe",
    "PythonVersionProbe",
    "check_databricks_sdk_version",
    "check_python_version",
    "check_serverless_env_version",
    "probe_databricks_sdk_version",
    "probe_python_version",
]
