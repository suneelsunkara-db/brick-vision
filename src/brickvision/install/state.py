"""Install state — N106.

Per [`docs/19-local-development.md`](../../../docs/19-local-development.md) §15.5
``brickvision install --resume-from <step_id>`` requires a
deterministic record of every install step's outcome so the
resumed run can pick up after the last clean step. This module
provides:

- ``InstallStep``    — one canonical step in the install sequence.
- ``InstallEvent``   — one outcome record signed + appended to
  the local ``./install/events.jsonl`` and (in workspace mode) to
  ``<bv>.install.events``.
- ``InstallState``   — the file-system writer the runner uses.
- ``replay_state``   — pure function that consumes a sequence of
  events and returns the resumable step id.

The runner emits one ``InstallEvent`` per step:

- ``status="started"``   — about to run; idempotent re-emits OK.
- ``status="succeeded"`` — step completed cleanly.
- ``status="failed"``    — step raised; resume picks up *here*.
- ``status="skipped"``   — explicitly skipped via ``--skip``.

State is append-only (P7). Replay is by reading the JSONL and
returning the most recent ``succeeded`` boundary.
"""

from __future__ import annotations

import dataclasses
import json
import time
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

InstallStatus = Literal["started", "succeeded", "failed", "skipped"]


@dataclasses.dataclass(frozen=True, slots=True)
class InstallStep:
    """One canonical step in the install runbook."""

    step_id: str
    description: str


@dataclasses.dataclass(frozen=True, slots=True)
class InstallEvent:
    """One install-step outcome row."""

    event_id: str
    install_id: str
    step_id: str
    status: InstallStatus
    detail: str
    ts_ms: int

    def to_jsonl(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True)


class InstallState:
    """File-system append-only writer (the local mirror of
    ``<bv>.install.events``)."""

    def __init__(self, path: Path, *, install_id: str | None = None) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._install_id = install_id or _read_or_create_install_id(path.parent)

    @property
    def install_id(self) -> str:
        return self._install_id

    @property
    def path(self) -> Path:
        return self._path

    def emit(
        self,
        *,
        step_id: str,
        status: InstallStatus,
        detail: str = "",
    ) -> InstallEvent:
        evt = InstallEvent(
            event_id=f"install_event:{uuid.uuid4().hex}",
            install_id=self._install_id,
            step_id=step_id,
            status=status,
            detail=detail,
            ts_ms=int(time.time() * 1000),
        )
        with self._path.open("a") as f:
            f.write(evt.to_jsonl())
            f.write("\n")
        return evt

    def read(self) -> list[InstallEvent]:
        if not self._path.exists():
            return []
        out: list[InstallEvent] = []
        for line in self._path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out.append(InstallEvent(**row))
        return out


def _read_or_create_install_id(dir_: Path) -> str:
    id_path = dir_ / "install_id"
    if id_path.exists():
        return id_path.read_text().strip()
    install_id = f"install:{uuid.uuid4().hex}"
    id_path.write_text(install_id)
    return install_id


def replay_state(
    *,
    events: Iterable[InstallEvent],
    steps: Sequence[InstallStep],
) -> tuple[set[str], str | None]:
    """Return ``(succeeded_step_ids, next_unfinished_step_id)``.

    Resume rule: pick the first step in ``steps`` that has not yet
    been emitted with ``status='succeeded'``. This is intentionally
    permissive about ``failed``/``skipped`` — the runner re-runs
    those — and intentionally strict about ``succeeded`` — once a
    step is succeeded, the resumed run skips it.
    """

    succeeded: set[str] = set()
    for evt in events:
        if evt.status == "succeeded":
            succeeded.add(evt.step_id)

    next_step: str | None = None
    for step in steps:
        if step.step_id not in succeeded:
            next_step = step.step_id
            break
    return succeeded, next_step


__all__ = [
    "InstallEvent",
    "InstallState",
    "InstallStatus",
    "InstallStep",
    "replay_state",
]
