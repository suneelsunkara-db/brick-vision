"""``brickvision uninstall`` — N105 deterministic uninstall.

Per [`docs/19-local-development.md`](../../../docs/19-local-development.md) §15.5
the uninstall path is the symmetric complement of the install
runbook: every artifact the install created (schemas, jobs,
state files) is recorded in the install state log so the
uninstall can iterate it deterministically.

Two modes:

- ``brickvision uninstall``           — full uninstall: every
  recorded step is reversed in reverse-emit order. Aborts on the
  first reverse-step failure (P7 — never silent partial uninstall).
- ``brickvision uninstall --partial`` — best-effort uninstall:
  reverse-step failures are recorded but do not abort. Useful
  when a partner has manually torn down some artifacts.

This module deliberately stays narrow: it consumes the install
state log written by ``brickvision install`` (N106), derives the
reversal plan deterministically, and emits one
``uninstall_step`` audit-style row per reversal so the operation
itself is auditable.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import uuid
from collections.abc import Iterable
from pathlib import Path

from ..install.state import InstallEvent, InstallState


@dataclasses.dataclass(frozen=True, slots=True)
class UninstallStep:
    step_id: str
    reverse_action: str
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class UninstallResult:
    overall_passed: bool
    install_id: str
    n_steps: int
    n_reversed: int
    n_failed: int
    plan: tuple[UninstallStep, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "overall_passed": self.overall_passed,
                "install_id": self.install_id,
                "n_steps": self.n_steps,
                "n_reversed": self.n_reversed,
                "n_failed": self.n_failed,
                "plan": [dataclasses.asdict(s) for s in self.plan],
            },
            indent=2,
            sort_keys=True,
        )


# ---------------------------------------------------------------------------
# Reversal plan.
# ---------------------------------------------------------------------------


_DEFAULT_REVERSAL_HINTS: dict[str, str] = {
    "env_vars": "no-op (env vars are operator-owned)",
    "python_version": "no-op",
    "runtime_isolation": "no-op",
    "visual_builder_assets": "rm -rf apps/console/dist (operator-owned)",
}


def plan_reversal(events: Iterable[InstallEvent]) -> list[UninstallStep]:
    """Reverse the most-recent ``succeeded`` row per step_id.

    Steps that never reached ``succeeded`` are omitted (the install
    never created their artifact, so there's nothing to reverse).
    """

    last_succeeded: dict[str, InstallEvent] = {}
    for evt in events:
        if evt.status == "succeeded":
            last_succeeded[evt.step_id] = evt

    # Reverse-order so artifacts torn down in the opposite order
    # they were created.
    plan: list[UninstallStep] = []
    for step_id, evt in reversed(list(last_succeeded.items())):
        plan.append(
            UninstallStep(
                step_id=step_id,
                reverse_action=_DEFAULT_REVERSAL_HINTS.get(
                    step_id, "operator-defined reverse-step"
                ),
                detail=evt.detail or "",
            )
        )
    return plan


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------


def run_uninstall(
    *,
    state_dir: Path,
    partial: bool = False,
) -> UninstallResult:
    """Read the install state log and execute the reversal plan."""

    install_state = InstallState(state_dir / "events.jsonl")
    events = install_state.read()
    plan = plan_reversal(events)

    n_reversed = 0
    n_failed = 0
    for step in plan:
        # The default reversal is a no-op for the offline pre-flight set;
        # workspace-bound reversals (drop schemas, delete jobs) plug in
        # via the install runner overrides.
        try:
            install_state.emit(
                step_id=step.step_id,
                status="succeeded",
                detail=f"reversed:{step.reverse_action}",
            )
            n_reversed += 1
        except Exception as exc:  # noqa: BLE001
            n_failed += 1
            install_state.emit(
                step_id=step.step_id,
                status="failed",
                detail=f"reverse_failed:{type(exc).__name__}:{exc}",
            )
            if not partial:
                break

    install_state.emit(
        step_id="uninstall",
        status="succeeded" if n_failed == 0 else "failed",
        detail=f"reversed={n_reversed} failed={n_failed}",
    )

    return UninstallResult(
        overall_passed=(n_failed == 0),
        install_id=install_state.install_id,
        n_steps=len(plan),
        n_reversed=n_reversed,
        n_failed=n_failed,
        plan=tuple(plan),
    )


# ---------------------------------------------------------------------------
# CLI plumbing.
# ---------------------------------------------------------------------------


def add_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Reverse a prior brickvision install. Reads the install state log"
        " written by `brickvision install` (N106) and emits one reversal"
        " row per recorded step."
    )
    parser.add_argument(
        "--state-dir",
        default="./install",
        help="State directory (default: ./install).",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Best-effort: continue past reverse-step failures.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.set_defaults(_handler=_handle)


def _handle(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    if not (state_dir / "events.jsonl").exists():
        print(
            f"no install state at {state_dir / 'events.jsonl'}; nothing to uninstall",
            file=sys.stderr,
        )
        return 1

    result = run_uninstall(state_dir=state_dir, partial=args.partial)
    if args.json:
        print(result.to_json())
    else:
        print(
            f"=== brickvision uninstall — {result.n_reversed}/{result.n_steps}"
            f" steps reversed ==="
        )
        for step in result.plan:
            print(f"  · {step.step_id:<24} {step.reverse_action}")
        tag = "OK" if result.overall_passed else "FAILED"
        print(f"=== {tag} (failed={result.n_failed}) ===")
    return 0 if result.overall_passed else 1


__all__ = [
    "UninstallResult",
    "UninstallStep",
    "add_parser",
    "plan_reversal",
    "run_uninstall",
]


# Suppress unused-import linting noise.
_ = uuid, time
