"""``brickvision install`` — N74 deterministic install runbook.

Per ``docs/19-local-development.md`` §15.5. The CLI is **the single
deterministic install path**; partners do not invoke
``databricks bundle deploy`` directly. Each pre-flight is a P7 hard
gate: on any miss, the CLI emits a typed ``Question`` and aborts
(no silent partial install).

Pre-flights are run as a list of ``PreFlight`` objects — small
``(check_id, runner, reason_code)`` tuples — so the ordering is
explicit and easy to audit. Each runner returns either ``None`` on
success or a ``PreFlightFailure`` carrying the typed
``ReasonCode`` + a partner-facing ``suggested_next_action``.

The default runner set ships the *deterministic, offline-friendly*
checks (env vars, schema declarations, lockfile shape). Partners
plug in workspace-bound runners — Databricks SDK, Vector Search,
MLflow — by passing their own ``Sequence[PreFlight]`` to
``run_install``. The CLI loads them from ``BV_INSTALL_PREFLIGHT_PATH``
when set, and otherwise runs the offline default set.

This module deliberately does **not** import the Databricks SDK at
the top level so the CLI works in air-gapped + CI environments
without a workspace handle.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from brickvision_runtime.failures import Question, ReasonCode


@dataclasses.dataclass(frozen=True, slots=True)
class PreFlightFailure:
    """One typed pre-flight miss."""

    reason_code: ReasonCode
    suggested_next_action: str
    detail: str = ""


PreFlightRunner = Callable[[], PreFlightFailure | None]


@dataclasses.dataclass(frozen=True, slots=True)
class PreFlight:
    """One named pre-flight gate."""

    check_id: str
    runner: PreFlightRunner
    description: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class InstallResult:
    """Aggregate result of one ``brickvision install`` invocation."""

    overall_passed: bool
    elapsed_ms: int
    checks_run: tuple[str, ...]
    failures: tuple[tuple[str, PreFlightFailure], ...]
    questions: tuple[Question, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "overall_passed": self.overall_passed,
                "elapsed_ms": self.elapsed_ms,
                "checks_run": list(self.checks_run),
                "failures": [
                    {
                        "check_id": cid,
                        "reason_code": f.reason_code.value,
                        "suggested_next_action": f.suggested_next_action,
                        "detail": f.detail,
                    }
                    for cid, f in self.failures
                ],
                "questions": [q.to_delta_row() for q in self.questions],
            },
            indent=2,
            sort_keys=True,
        )


# ---------------------------------------------------------------------------
# Deterministic offline pre-flights (the defaults).
# ---------------------------------------------------------------------------


_REQUIRED_ENV_VARS: tuple[str, ...] = (
    "BV_MODE",
    "BV_CATALOG",
    "BV_SCHEMA",
)


def _check_env_vars() -> PreFlightFailure | None:
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if not missing:
        return None
    return PreFlightFailure(
        reason_code=ReasonCode.MODEL_ROLE_NOT_RESOLVED,  # closest typed code in the open enum
        suggested_next_action=(
            "set the following env vars in your install profile: "
            f"{', '.join(missing)}"
        ),
        detail=f"missing={missing}",
    )


def _check_python_version() -> PreFlightFailure | None:
    if sys.version_info < (3, 10):
        return PreFlightFailure(
            reason_code=ReasonCode.PYTHON_VERSION_TOO_OLD,
            suggested_next_action="install Python ≥ 3.10 (UC Functions floor)",
            detail=f"found={sys.version}",
        )
    return None


def _check_runtime_isolation() -> PreFlightFailure | None:
    """The N3 P7 ratchet — runtime substrate must not import the
    build-time package at module load time.

    We re-run the static scan instead of trusting the prior artifact
    so a stale artifact can't pass a regressed install. Lazy imports
    inside function/method bodies are allowed: they don't fire at
    runtime substrate import time, so they cannot drag the build-time
    package into a deployed harness.
    """

    repo_root = Path(__file__).resolve().parents[3]
    runtime_root = repo_root / "src" / "brickvision_runtime"
    if not runtime_root.exists():
        return None

    violation = _scan_runtime_top_level_brickvision_imports(runtime_root)
    if violation is None:
        return None
    py_path, lineno, line_text = violation
    return PreFlightFailure(
        reason_code=ReasonCode.MODEL_ROLE_NOT_RESOLVED,
        suggested_next_action=(
            "remove the top-level brickvision import from "
            f"{py_path.relative_to(repo_root)} (move it inside the function "
            "body if it's only needed at call time); the runtime substrate "
            "must stay independent of the build-time pkg at module load"
        ),
        detail=(
            f"path={py_path.relative_to(repo_root)} line={lineno} "
            f"text={line_text.strip()}"
        ),
    )


def _scan_runtime_top_level_brickvision_imports(
    runtime_root: Path,
) -> tuple[Path, int, str] | None:
    """AST-based runtime-isolation scan.

    Returns the first ``(file, lineno, line_text)`` that imports a
    ``brickvision.*`` package at module top level (NOT inside a
    function or class body); returns ``None`` if the tree is clean.

    Imports of ``brickvision_runtime.*`` are explicitly allowed (the
    runtime substrate may import itself); only the build-time package
    ``brickvision`` and its sub-packages are forbidden.

    On a per-file ``SyntaxError`` we fall back to a conservative
    regex check so a half-edited file still surfaces a violation
    rather than silently passing.
    """

    import ast

    for py in sorted(runtime_root.rglob("*.py")):
        try:
            source = py.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(py))
        except SyntaxError:
            # Fallback: regex over physical lines so a syntax-broken
            # file still gets scanned. Slightly stricter than the AST
            # walker because we can't tell scope from raw text alone.
            import re as _re

            fallback = _re.compile(
                r"^\s*(?:from\s+brickvision(?:\.|\s)|import\s+brickvision(?:\s|\.|$))"
            )
            for lineno, raw in enumerate(source.splitlines(), start=1):
                line = raw.split("#", 1)[0]
                if not fallback.match(line):
                    continue
                if line.lstrip().startswith(
                    ("from brickvision_runtime", "import brickvision_runtime")
                ):
                    continue
                return (py, lineno, raw)
            continue

        # Module-level (top-level) statements only.
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name == "brickvision" or name.startswith("brickvision."):
                        if name == "brickvision_runtime" or name.startswith(
                            "brickvision_runtime."
                        ):
                            continue
                        return (py, node.lineno, ast.unparse(node))
            elif isinstance(node, ast.ImportFrom):
                # ``from . import foo`` / ``from .x import y`` are relative
                # imports (``module is None`` or ``level > 0``); never a
                # cross-package leak — skip them.
                if node.level and node.level > 0:
                    continue
                module = node.module or ""
                if module == "brickvision" or module.startswith("brickvision."):
                    if module == "brickvision_runtime" or module.startswith(
                        "brickvision_runtime."
                    ):
                        continue
                    return (py, node.lineno, ast.unparse(node))
    return None


def _check_visual_builder_assets() -> PreFlightFailure | None:
    """``pre_flight.visual_builder_assets`` (v0.7.6.9 retarget).

    The React SPA must be built (``apps/console/dist/index.html``
    exists) before ``brickvision install`` packages it for the
    Databricks App. We only flag a miss when the partner has
    actually opted into the visual builder via
    ``BV_VISUAL_BUILDER_ENABLED=true``.
    """

    if os.environ.get("BV_VISUAL_BUILDER_ENABLED", "false").lower() != "true":
        return None
    repo_root = Path(__file__).resolve().parents[3]
    dist = repo_root / "apps" / "console" / "dist" / "index.html"
    if dist.exists():
        return None
    return PreFlightFailure(
        reason_code=ReasonCode.VS_OUT_OF_BAND_PROVISIONING_REQUIRED,
        suggested_next_action=(
            "build the SPA: pnpm --filter @brickvision/console build "
            "(produces apps/console/dist/)"
        ),
        detail=f"missing={dist}",
    )


# ---------------------------------------------------------------------------
# v0.7.7 Capability Graph install pre-flights (N180)
# ---------------------------------------------------------------------------
#
# Per docs/19-local-development.md §15.5 the v0.7.7 install adds 4 hard
# gates that must pass before the indexer Job can run safely. Each
# runner here builds the workspace probe via
# ``brickvision.install.preflight.capability_graph_probes`` and runs
# the pure check function from
# ``brickvision.install.preflight.capability_graph``; the probe builder
# is the only place ``databricks-sdk`` enters the install code path,
# and ``BV_DRY_RUN=true`` short-circuits each builder to a JSON
# fixture so offline CI can exercise the gate logic without a real
# workspace.
#
# The 4 gates are gated by ``BV_CAPABILITY_GRAPH_ENABLED`` (default
# ``true``); setting it to ``false`` skips all 4 (only useful for
# pre-v0.7.7 partner installs that have not yet provisioned the
# capability-graph schema).


def _capability_graph_enabled() -> bool:
    """Default-on per the v0.7.7 release contract."""

    return os.environ.get("BV_CAPABILITY_GRAPH_ENABLED", "true").lower() not in (
        "0",
        "false",
        "no",
    )


def _first_failure_or_none(
    failures: Sequence[PreFlightFailure],
) -> PreFlightFailure | None:
    """The PreFlight runner contract returns at most one failure per
    runner; check functions return a list so they can surface
    multi-aspect misses. We surface the FIRST failure (the others are
    reported via ``detail`` once the partner fixes the first)."""

    return failures[0] if failures else None


def _probe_unavailable_failure(
    *,
    reason_code: ReasonCode,
    check_id: str,
) -> PreFlightFailure:
    """Standardised failure when a probe builder returns ``None`` —
    typically a missing dry-run fixture or a workspace SDK error.
    The check functions can't run on a ``None`` probe, so this is a
    distinct failure mode (probe collection itself failed)."""

    return PreFlightFailure(
        reason_code=reason_code,
        suggested_next_action=(
            f"unable to collect the workspace probe for {check_id!r};"
            " verify Databricks auth (BV_INSTALL_AUTH_TOKEN /"
            " profile) is configured AND BV_INDEXER_WAREHOUSE_ID"
            " resolves; under BV_DRY_RUN=true verify the fixture"
            " under tests/fixtures/install_preflight/capability_graph/"
            " exists"
        ),
        detail=f"probe_unavailable check_id={check_id}",
    )


def _check_indexer_sp_provisioned() -> PreFlightFailure | None:
    """Pre-flight: ``bv_indexer_sp`` exists + distinct from ``bv_app_sp``."""

    from brickvision.install.preflight.capability_graph import (  # noqa: PLC0415
        IndexerSPSpec,
        check_indexer_sp_provisioned,
    )
    from brickvision.install.preflight.capability_graph_probes import (  # noqa: PLC0415
        build_indexer_sp_probe,
    )

    spec = IndexerSPSpec()
    probe = build_indexer_sp_probe(spec=spec)
    if probe is None:
        return _probe_unavailable_failure(
            reason_code=ReasonCode.INDEXER_SP_NOT_PROVISIONED,
            check_id="indexer_sp_provisioned",
        )
    return _first_failure_or_none(
        check_indexer_sp_provisioned(spec=spec, probe=probe)
    )


def _check_indexer_budget_namespace_isolated() -> PreFlightFailure | None:
    """Pre-flight: ``app`` + ``indexer`` budget namespaces non-overlapping."""

    from brickvision.install.preflight.capability_graph import (  # noqa: PLC0415
        BudgetNamespaceSpec,
        check_budget_namespace_isolated,
    )
    from brickvision.install.preflight.capability_graph_probes import (  # noqa: PLC0415
        build_budget_namespace_probe,
    )

    spec = BudgetNamespaceSpec()
    probe = build_budget_namespace_probe(spec=spec)
    if probe is None:
        return _probe_unavailable_failure(
            reason_code=ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED,
            check_id="indexer_budget_namespace_isolated",
        )
    return _first_failure_or_none(
        check_budget_namespace_isolated(spec=spec, probe=probe)
    )


def _check_uc_schema_capability_graph_ownership() -> PreFlightFailure | None:
    """Pre-flight: ``<BV_CATALOG>.<BV_SCHEMA>`` owned by indexer SP, app SP READ-only."""

    from brickvision.install.preflight.capability_graph import (  # noqa: PLC0415
        IndexerSPSpec,
        UCSchemaSpec,
        check_uc_schema_capability_graph_ownership,
    )
    from brickvision.install.preflight.capability_graph_probes import (  # noqa: PLC0415
        build_indexer_sp_probe,
        build_uc_schema_probe,
    )

    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    sp_probe = build_indexer_sp_probe(spec=IndexerSPSpec())
    indexer_principal = (
        sp_probe.indexer_sp_application_id
        if sp_probe and sp_probe.indexer_sp_application_id
        else "bv_indexer_sp"
    )
    app_principal = (
        sp_probe.app_sp_application_id
        if sp_probe and sp_probe.app_sp_application_id
        else "bv_app_sp"
    )
    spec = UCSchemaSpec(
        schema_full_name=f"{catalog}.{schema}",
        expected_owner=indexer_principal,
        expected_owner_aliases=("bv_indexer_sp",),
        app_sp_display_name=app_principal,
        app_sp_aliases=("bv_app_sp",),
    )
    probe = build_uc_schema_probe(spec=spec)
    if probe is None:
        return _probe_unavailable_failure(
            reason_code=ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID,
            check_id="uc_schema_capability_graph_ownership",
        )
    return _first_failure_or_none(
        check_uc_schema_capability_graph_ownership(spec=spec, probe=probe)
    )


def _check_vector_search_endpoint_grants() -> PreFlightFailure | None:
    """Pre-flight: VS endpoint per-index grants are READ for app, WRITE for indexer."""

    from brickvision.install.preflight.capability_graph import (  # noqa: PLC0415
        IndexerSPSpec,
        VSGrantSpec,
        check_vector_search_endpoint_grants,
    )
    from brickvision.install.preflight.capability_graph_probes import (  # noqa: PLC0415
        build_indexer_sp_probe,
        build_vs_grants_probe,
    )

    sp_probe = build_indexer_sp_probe(spec=IndexerSPSpec())
    indexer_principal = (
        sp_probe.indexer_sp_application_id
        if sp_probe and sp_probe.indexer_sp_application_id
        else "bv_indexer_sp"
    )
    app_principal = (
        sp_probe.app_sp_application_id
        if sp_probe and sp_probe.app_sp_application_id
        else "bv_app_sp"
    )
    spec = VSGrantSpec(
        indexer_sp_display_name=indexer_principal,
        indexer_sp_aliases=("bv_indexer_sp",),
        app_sp_display_name=app_principal,
        app_sp_aliases=("bv_app_sp",),
    )
    probe = build_vs_grants_probe(spec=spec)
    if probe is None:
        return _probe_unavailable_failure(
            reason_code=ReasonCode.VS_ENDPOINT_GRANTS_MIXED,
            check_id="vector_search_endpoint_grants",
        )
    return _first_failure_or_none(
        check_vector_search_endpoint_grants(spec=spec, probe=probe)
    )


def default_preflights() -> list[PreFlight]:
    """Offline-friendly default pre-flight set.

    Includes the v0.7.7 capability-graph gates by default; set
    ``BV_CAPABILITY_GRAPH_ENABLED=false`` to skip them (only useful
    for pre-v0.7.7 installs).
    """

    base: list[PreFlight] = [
        PreFlight("env_vars", _check_env_vars, "required env vars present"),
        PreFlight("python_version", _check_python_version, "python ≥ 3.10"),
        PreFlight(
            "runtime_isolation",
            _check_runtime_isolation,
            "brickvision_runtime imports no brickvision (P7)",
        ),
        PreFlight(
            "visual_builder_assets",
            _check_visual_builder_assets,
            "apps/console/dist/ present when BV_VISUAL_BUILDER_ENABLED=true",
        ),
    ]

    if _capability_graph_enabled():
        base.extend(
            [
                PreFlight(
                    "indexer_sp_provisioned",
                    _check_indexer_sp_provisioned,
                    "bv_indexer_sp exists + distinct from bv_app_sp",
                ),
                PreFlight(
                    "indexer_budget_namespace_isolated",
                    _check_indexer_budget_namespace_isolated,
                    "app + indexer budget namespaces non-overlapping",
                ),
                PreFlight(
                    "uc_schema_capability_graph_ownership",
                    _check_uc_schema_capability_graph_ownership,
                    "<bv>.capability_graph owned by bv_indexer_sp; bv_app_sp READ-only",
                ),
                PreFlight(
                    "vector_search_endpoint_grants",
                    _check_vector_search_endpoint_grants,
                    "VS endpoint per-index grants: indexer WRITE, app READ",
                ),
            ]
        )

    return base


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------


def run_install(
    *,
    preflights: Sequence[PreFlight] | None = None,
    raised_by: str = "agent:brickvision-install",
    on_step_started: Callable[[str], object] | None = None,
    on_step_succeeded: Callable[[str], object] | None = None,
    on_step_failed: Callable[[str, PreFlightFailure], object] | None = None,
) -> InstallResult:
    """Run every pre-flight; return an aggregate result.

    Failed pre-flights produce typed ``Question``s; the install
    aborts with ``overall_passed=False`` when any pre-flight fails
    (P7 hard gate).

    The ``on_step_*`` callbacks are the install-state hook used by
    ``brickvision install --resume-from``: each step emits a
    ``started`` then either ``succeeded`` or ``failed`` row to the
    install-events log so the next invocation knows where to pick
    up.
    """

    started = time.time()
    preflights = list(preflights) if preflights is not None else default_preflights()
    failures: list[tuple[str, PreFlightFailure]] = []
    questions: list[Question] = []

    for pf in preflights:
        if on_step_started is not None:
            try:
                on_step_started(pf.check_id)
            except Exception:  # noqa: BLE001
                pass

        try:
            result = pf.runner()
        except Exception as exc:  # noqa: BLE001 - partner-defined runners
            result = PreFlightFailure(
                reason_code=ReasonCode.MODEL_ROLE_NOT_RESOLVED,
                suggested_next_action=(
                    f"pre-flight {pf.check_id!r} runner raised {type(exc).__name__}: {exc}"
                ),
                detail=f"runner_exception={type(exc).__name__}",
            )

        if result is None:
            if on_step_succeeded is not None:
                try:
                    on_step_succeeded(pf.check_id)
                except Exception:  # noqa: BLE001
                    pass
            continue

        failures.append((pf.check_id, result))
        questions.append(
            Question.open(
                subject=f"pre_flight:{pf.check_id}",
                text=f"{pf.check_id}: {result.detail or result.reason_code.value}",
                suggested_next_action=result.suggested_next_action,
                raised_by=raised_by,
                reason_code=result.reason_code.value,
                metadata={"check_id": pf.check_id},
            )
        )
        if on_step_failed is not None:
            try:
                on_step_failed(pf.check_id, result)
            except Exception:  # noqa: BLE001
                pass

    elapsed_ms = int((time.time() - started) * 1000)
    return InstallResult(
        overall_passed=not failures,
        elapsed_ms=elapsed_ms,
        checks_run=tuple(p.check_id for p in preflights),
        failures=tuple(failures),
        questions=tuple(questions),
    )


# ---------------------------------------------------------------------------
# CLI plumbing.
# ---------------------------------------------------------------------------


def add_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Run the deterministic install pre-flight set. Aborts on the first "
        "failed gate; emits typed Questions for every miss."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        help="Skip a pre-flight by check_id. Repeatable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the planned pre-flight sequence without running any."
            " Useful for offline review."
        ),
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help=(
            "Resume from a specific check_id (skips every step before it)."
            " Reads ./install/events.jsonl when present so the runner does"
            " not re-run already-succeeded steps."
        ),
    )
    parser.add_argument(
        "--state-dir",
        default="./install",
        help="Directory for install_id + events.jsonl (default: ./install).",
    )
    parser.set_defaults(_handler=_handle)


def _filter_preflights(
    *,
    preflights: list[PreFlight],
    skip: set[str],
    resume_from: str | None,
    state_dir: Path,
) -> list[PreFlight]:
    if skip:
        preflights = [p for p in preflights if p.check_id not in skip]

    # Resume contract: read the install-state log and skip any
    # check_id already at status='succeeded'.
    state_log = state_dir / "events.jsonl"
    if state_log.exists():
        succeeded: set[str] = set()
        for line in state_log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("status") == "succeeded" and isinstance(row.get("step_id"), str):
                succeeded.add(row["step_id"])
        if succeeded:
            preflights = [p for p in preflights if p.check_id not in succeeded]

    if resume_from:
        idx = next(
            (i for i, p in enumerate(preflights) if p.check_id == resume_from),
            None,
        )
        if idx is None:
            raise SystemExit(f"--resume-from {resume_from!r} not found in pre-flights")
        preflights = preflights[idx:]

    return preflights


def _handle(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    preflights = _filter_preflights(
        preflights=default_preflights(),
        skip=set(args.skip),
        resume_from=args.resume_from,
        state_dir=state_dir,
    )

    if args.dry_run:
        plan = [
            {"check_id": p.check_id, "description": p.description}
            for p in preflights
        ]
        if args.json:
            print(json.dumps({"dry_run": True, "plan": plan}, indent=2))
        else:
            print(f"=== brickvision install --dry-run ({len(plan)} pre-flights) ===")
            for entry in plan:
                print(f"  · {entry['check_id']:<24} {entry['description']}")
        return 0

    # Lazy-import so the offline tier doesn't pull state machinery
    # unless the user opts into it (state-dir defaults to ./install
    # which is created lazily by emit()).
    from ..install.state import InstallState

    install_state: InstallState | None = None
    if state_dir is not None:
        install_state = InstallState(state_dir / "events.jsonl")

    result = run_install(
        preflights=preflights,
        on_step_started=(
            (lambda cid: install_state.emit(step_id=cid, status="started"))
            if install_state else None
        ),
        on_step_succeeded=(
            (lambda cid: install_state.emit(step_id=cid, status="succeeded"))
            if install_state else None
        ),
        on_step_failed=(
            (lambda cid, fail: install_state.emit(
                step_id=cid, status="failed",
                detail=fail.reason_code.value,
            ))
            if install_state else None
        ),
    )

    if args.json:
        out = json.loads(result.to_json())
        if install_state is not None:
            out["install_id"] = install_state.install_id
        print(json.dumps(out, indent=2))
    else:
        print(f"=== brickvision install — {len(result.checks_run)} pre-flights ===")
        passed_ids = set(result.checks_run) - {cid for cid, _ in result.failures}
        for cid in result.checks_run:
            tag = "OK " if cid in passed_ids else "FAIL"
            print(f"  [{tag}] {cid}")
        for cid, failure in result.failures:
            print(
                f"\n  ↳ {cid}: {failure.reason_code.value}\n"
                f"     suggested: {failure.suggested_next_action}\n"
                f"     detail:    {failure.detail}"
            )
        print(f"=== {'OK' if result.overall_passed else 'FAILED'} "
              f"({result.elapsed_ms}ms) ===")
    return 0 if result.overall_passed else 1


__all__ = [
    "InstallResult",
    "PreFlight",
    "PreFlightFailure",
    "PreFlightRunner",
    "add_parser",
    "default_preflights",
    "run_install",
]
