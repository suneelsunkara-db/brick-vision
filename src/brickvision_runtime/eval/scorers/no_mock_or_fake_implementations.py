"""N189 — ``NoMockOrFakeImplementations`` scorer.

Backstop for **discipline rule 15** ("Production-only code. No mocks,
no fakes, no abstraction-for-tests") locked in
``docs/01-overview.md`` §0 and detailed in
``docs/10-generation-philosophy.md`` §8.6 + ``docs/19-local-development.md``
§15.2.1 + ``docs/17-eval-framework.md`` §13.3.

The scorer is a pure-AST static linter that walks every ``.py`` under
the configured production roots (default ``src/brickvision`` +
``src/brickvision_runtime``) and emits violations against four
mechanical patterns:

1. **Forbidden class names** — any ``class Fake*``, ``class Mock*``,
   ``class Stub*``, or ``class Dummy*`` (case-sensitive prefix match
   against ``ast.ClassDef.name``). Reason code:
   ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE``.

2. **Forbidden function names** — any ``def fake_*``, ``def mock_*``,
   ``def stub_*``, or ``def dummy_*`` at module or class scope (also
   inside nested ``ast.FunctionDef`` / ``ast.AsyncFunctionDef``).
   Reason code: ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE``.

3. **Forbidden file names** — any module whose stem ends with
   ``_stub``, ``_fake``, ``_mock``, or ``_dummy``. Reason code:
   ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE``.

4. **Protocol-as-mock-seam** — every public ``typing.Protocol``
   subclass (name does NOT start with ``_``) must have at least one
   concrete production subclass in the same root that does NOT start
   with ``Fake``/``Mock``/``Stub``/``Dummy``. A Protocol with zero
   concrete production subclasses is a violation; a Protocol whose
   concrete subclasses are *all* mocks is a violation. Reason code:
   ``PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES``.

Private Protocols (``_`` prefix, e.g. ``_MlflowModule``,
``_DataFrameLike``) are exempt — they are structural-typing facades
over a real third-party module's public API and are never satisfied by
a mock at runtime.

Each violation is reported as a ``(file, lineno, detail)`` triple in
``ScorerResult.details["violations"]``. An optional
``soft_warn_files`` argument moves matched-file violations into
``ScorerResult.details["soft_warnings"]`` instead of failing the
scorer; the parameter is retained as a *future* surgical-exemption
landing point but the v0.7.7-cascade transitional allowance is
**closed** — the default :data:`_V077_CASCADE_SOFT_WARN_FILES` is an
empty frozenset, and any future violation must hard-fail CI rather
than be tolerated via this path.

This scorer is itself listed in the ``_SELF_ALLOWLIST`` because it
mentions the forbidden prefixes by string-literal value.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterable, Sequence
from pathlib import Path

from brickvision_runtime.eval.scorers import ScorerResult, register_scorer
from brickvision_runtime.failures import ReasonCode

_FORBIDDEN_CLASS_PREFIXES: tuple[str, ...] = ("Fake", "Mock", "Stub", "Dummy")
_FORBIDDEN_FUNC_PREFIXES: tuple[str, ...] = ("fake_", "mock_", "stub_", "dummy_")
_FORBIDDEN_FILENAME_SUFFIXES: tuple[str, ...] = ("_stub", "_fake", "_mock", "_dummy")

_DEFAULT_ROOTS: tuple[str, ...] = (
    "src/brickvision",
    "src/brickvision_runtime",
)

# This file mentions the forbidden tokens as string literals only;
# always exempt from its own scan.
_SELF_ALLOWLIST: frozenset[str] = frozenset(
    {
        "src/brickvision_runtime/eval/scorers/no_mock_or_fake_implementations.py",
    }
)

# v0.7.7-cascade soft-warn allowance — CLOSED under N189.
#
# All 13 retirement items enumerated in
# ``docs/24-pending-tasks-tracker.md`` §24.3 sub-tables A + B have
# landed; the constant is now an empty frozenset and discipline rule
# 15 is in hard-fail mode. The constant intentionally stays in the
# module surface as the (currently unused) landing point for *future*
# surgical exemptions; it must NEVER be repopulated to silence a
# fresh violation.
#
# Closed under N189 (sub-table A — capability_graph cascade):
#   - capability_graph/embed.py — RETIRED ``EmbeddingClient(Protocol)``;
#     ``embed_batch`` now invokes Foundation Model Serving directly via
#     ``databricks.sdk`` (lazy-imported), short-circuited by
#     ``BV_FAKE_LLM=true`` to canned vectors at
#     ``tests/fixtures/capability_graph/canned_embeddings.json``.
#   - capability_graph/persist.py — RETIRED ``DeltaWriter(Protocol)``;
#     ``persist_snapshot`` now writes via Statement Execution (lazy
#     ``databricks.sdk``) with ``BV_DRY_RUN=true`` flushing the
#     rendered SQL + payload to
#     ``tests/fixtures/capability_graph/last_persist_payload.json``.
#   - capability_graph/vs_upsert.py — RETIRED ``VectorSearchClient(Protocol)``;
#     ``vs_upsert_embeddings`` now talks to
#     ``databricks.vector_search.client.VectorSearchClient``. ``BV_DRY_RUN``
#     logs upsert payloads to
#     ``tests/fixtures/capability_graph/last_vs_upsert_payload.json``.
#   - capability_graph/smoke.py — RETIRED ``VectorSearchRetriever(Protocol)``;
#     ``run_smoke`` now retrieves through the live VS SDK; ``BV_FAKE_LLM``
#     short-circuits to per-query canned hits at
#     ``tests/fixtures/capability_graph/canned_smoke_hits.json``.
#   - capability_graph/promote.py — RETIRED ``PromotionWriter(Protocol)``;
#     ``promote_snapshot`` now issues a single ``BEGIN; ... COMMIT;``
#     transaction via Statement Execution. ``BV_DRY_RUN`` writes the
#     rendered statements to
#     ``tests/fixtures/capability_graph/last_promote_payload.json``.
#   - capability_graph/retention.py — RETIRED ``LifecycleStore(Protocol)``;
#     ``run_retention`` now issues SELECT/UPDATE/DELETE via Statement
#     Execution, calls UC Volumes ``files.delete_directory`` for staging
#     cleanup, and uses ``BV_DRY_RUN`` to log the plan to
#     ``tests/fixtures/capability_graph/last_retention_payload.json``.
#   - capability_graph/graph_builder.py — RETIRED ``KgExtractor(Protocol)``;
#     blog-chunk → meta-skill mention extraction now routes through
#     ``brickvision_runtime.kg.extractor.kg_extractor`` (the production
#     ``kg_extractor`` symbolic role), gated by ``enable_blog_mentions``.
#     ``BV_FAKE_LLM=true`` short-circuits to canned mentions at
#     ``tests/fixtures/kg_extractor/canned_meta_skill_mentions.json``.
#
# Closed under N189 (sub-table B — pre-existing v0.7.5 / v0.7.6.x / Phase -1):
#   - src/brickvision/stages/agent_design_stub.py — DELETED;
#     scripts/phase_minus_1_gate.py now hosts the harness directly,
#     reading tests/fixtures/phase_minus_1/canonical_goals.yaml.
#   - telemetry/central_stub.py — DELETED (v0.7.7 cleanup); the
#     entire central-export pipeline (BatchExporter, ExportEnvelope,
#     PIIScanner, telemetry_central in-process aggregator) was
#     retired alongside it because no central endpoint exists or
#     is planned for v0.6 — only the local Delta sink remains.
#   - install/fms_retirement_calendar_refresher.py — DELETED (v0.7.7
#     over-architecture cleanup); the whole HTML-scraping nightly Job
#     + Delta config table + install pre-flight + reason code retired
#     in favour of a hardcoded retirement-date list in model_routing
#     defaults updated manually each BrickVision release. The 3
#     Protocols (HtmlFetcher, DeltaConfigWriter, Clock) and the
#     companion routing_table_retirement pre-flight are gone.
#   - context_engine/sub_agent.py — RETIRED ``fake_runner``
#     (renamed to ``synthetic_local_runner``); added
#     ``production_runner`` that dispatches via the harness
#     coordinator, and a ``make_runner()`` factory selected by
#     ``BV_FAKE_LLM=true``.
#   - harness/coordinator.py — RETIRED ``FakeLLMCoordinator``;
#     production code path now ships ``OpenAIAgentsCoordinator``
#     (wraps ``agents.Runner.run_sync``) and ``make_coordinator()``
#     selects via ``BV_FAKE_LLM`` between the live coordinator and a
#     canned-fixture invoker reading
#     ``tests/fixtures/coordinator/canned_responses.json``.
_V077_CASCADE_SOFT_WARN_FILES: frozenset[str] = frozenset()
"""N189 close — empty. Discipline rule 15 is now in hard-fail mode;
this constant stays as a compile-time landing point for *future*
surgical exemptions only, never as a long-lived allowance."""


# ---------------------------------------------------------------------------
# Internal violation record.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _Violation:
    file: str
    lineno: int
    kind: str  # "class" | "function" | "filename" | "protocol"
    name: str
    detail: str
    reason_code: str


def _to_dict(v: _Violation) -> dict[str, object]:
    return {
        "file": v.file,
        "lineno": v.lineno,
        "kind": v.kind,
        "name": v.name,
        "detail": v.detail,
        "reason_code": v.reason_code,
    }


# ---------------------------------------------------------------------------
# AST helpers.
# ---------------------------------------------------------------------------


def _starts_with_any(name: str, prefixes: Sequence[str]) -> bool:
    return any(name.startswith(p) for p in prefixes)


def _has_protocol_base(node: ast.ClassDef) -> bool:
    """Return True if ``node`` directly subclasses ``Protocol``.

    Matches ``class X(Protocol)``, ``class X(Protocol, Generic[T])``,
    or ``class X(typing.Protocol)``.
    """

    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
        if isinstance(base, ast.Subscript):
            value = base.value
            if isinstance(value, ast.Name) and value.id == "Protocol":
                return True
            if isinstance(value, ast.Attribute) and value.attr == "Protocol":
                return True
    return False


def _base_names(node: ast.ClassDef) -> tuple[str, ...]:
    """Return the simple names of ``node``'s direct bases."""

    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
        elif isinstance(base, ast.Subscript):
            value = base.value
            if isinstance(value, ast.Name):
                names.append(value.id)
            elif isinstance(value, ast.Attribute):
                names.append(value.attr)
    return tuple(names)


def _walk_function_defs(tree: ast.AST) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield every (sync + async) function def in ``tree``."""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _walk_class_defs(tree: ast.AST) -> Iterable[ast.ClassDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield node


# ---------------------------------------------------------------------------
# Per-file scanner.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _FileScan:
    file: str
    violations: tuple[_Violation, ...]
    public_protocols: tuple[tuple[str, int], ...]  # (name, lineno)
    concrete_classes_with_local_bases: tuple[tuple[str, tuple[str, ...], int], ...]
    # (subclass_name, parent_simple_names, lineno)


def _scan_one_file(*, rel_path: str, source: str) -> _FileScan:
    violations: list[_Violation] = []
    public_protocols: list[tuple[str, int]] = []
    concrete: list[tuple[str, tuple[str, ...], int]] = []

    # 1. File-name suffix check.
    stem = Path(rel_path).stem
    for suffix in _FORBIDDEN_FILENAME_SUFFIXES:
        if stem.endswith(suffix):
            violations.append(
                _Violation(
                    file=rel_path,
                    lineno=1,
                    kind="filename",
                    name=stem,
                    detail=(
                        f"module filename ends with {suffix!r}; production"
                        " modules must not be named *_stub/*_fake/*_mock/*_dummy."
                        " Move the file to tests/fixtures/ or rename it to its"
                        " production purpose"
                    ),
                    reason_code=ReasonCode.MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE.value,
                )
            )
            break

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        # Don't silently swallow; surface as a violation so the operator
        # can fix the syntax, but classify under filename-kind so the
        # reason code remains stable.
        violations.append(
            _Violation(
                file=rel_path,
                lineno=getattr(e, "lineno", 0) or 0,
                kind="filename",
                name=stem,
                detail=f"AST parse failed: {e}",
                reason_code=ReasonCode.MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE.value,
            )
        )
        return _FileScan(
            file=rel_path,
            violations=tuple(violations),
            public_protocols=(),
            concrete_classes_with_local_bases=(),
        )

    # 2. Class-name prefix check + Protocol harvest + concrete subclass harvest.
    for cls in _walk_class_defs(tree):
        if _starts_with_any(cls.name, _FORBIDDEN_CLASS_PREFIXES):
            violations.append(
                _Violation(
                    file=rel_path,
                    lineno=cls.lineno,
                    kind="class",
                    name=cls.name,
                    detail=(
                        f"class {cls.name!r} starts with a forbidden prefix"
                        " (Fake/Mock/Stub/Dummy); production code must"
                        " ship the real implementation. Move this class to"
                        " tests/fixtures/ and gate any test-only behavior in"
                        " the production code path through the BV_FAKE_LLM"
                        " or BV_DRY_RUN env-gate"
                    ),
                    reason_code=ReasonCode.MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE.value,
                )
            )

        if _has_protocol_base(cls):
            if not cls.name.startswith("_"):
                public_protocols.append((cls.name, cls.lineno))
            # Protocol classes themselves are never recorded as concrete
            # subclasses for the seam check.
            continue

        bases = _base_names(cls)
        if bases:
            concrete.append((cls.name, bases, cls.lineno))

    # 3. Function-name prefix check.
    for fn in _walk_function_defs(tree):
        if _starts_with_any(fn.name, _FORBIDDEN_FUNC_PREFIXES):
            violations.append(
                _Violation(
                    file=rel_path,
                    lineno=fn.lineno,
                    kind="function",
                    name=fn.name,
                    detail=(
                        f"function {fn.name!r} starts with a forbidden prefix"
                        " (fake_/mock_/stub_/dummy_); production code must"
                        " ship the real implementation. Move the body to"
                        " tests/fixtures/ and call it from a test file, or"
                        " inline a BV_FAKE_LLM/BV_DRY_RUN env-gate inside"
                        " the production function"
                    ),
                    reason_code=ReasonCode.MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE.value,
                )
            )

    return _FileScan(
        file=rel_path,
        violations=tuple(violations),
        public_protocols=tuple(public_protocols),
        concrete_classes_with_local_bases=tuple(concrete),
    )


# ---------------------------------------------------------------------------
# Cross-file Protocol-as-mock-seam check.
# ---------------------------------------------------------------------------


def _check_protocol_subclass_seam(
    *,
    scans: Sequence[_FileScan],
) -> list[_Violation]:
    """For every public Protocol, verify ≥1 non-mock concrete subclass.

    Subclass relationships are matched by simple-name lookup: any
    concrete class whose simple base-name matches the Protocol's
    simple name is treated as a subclass. Cross-package collisions
    (two unrelated Protocols of the same name) are vanishingly rare
    in this codebase; if introduced, ``_check_protocol_subclass_seam``
    will report the more conservative behavior (a non-mock subclass
    found anywhere clears the seam violation).
    """

    protocols: dict[str, tuple[str, int]] = {}
    for scan in scans:
        for name, lineno in scan.public_protocols:
            protocols[name] = (scan.file, lineno)

    subclasses_by_parent: dict[str, list[tuple[str, str, int]]] = {}
    for scan in scans:
        for sub_name, base_names, sub_lineno in scan.concrete_classes_with_local_bases:
            for parent in base_names:
                if parent in protocols:
                    subclasses_by_parent.setdefault(parent, []).append(
                        (sub_name, scan.file, sub_lineno)
                    )

    violations: list[_Violation] = []
    for proto_name, (proto_file, proto_lineno) in protocols.items():
        subs = subclasses_by_parent.get(proto_name, [])
        if not subs:
            violations.append(
                _Violation(
                    file=proto_file,
                    lineno=proto_lineno,
                    kind="protocol",
                    name=proto_name,
                    detail=(
                        f"public Protocol {proto_name!r} has zero concrete"
                        " production subclasses; the only way to satisfy it"
                        " at runtime is a test-only mock, which is the"
                        " 'Protocol-as-mock-seam' anti-pattern. Retire the"
                        " Protocol, ship the real wrapper directly, and"
                        " gate test-only behavior through BV_FAKE_LLM /"
                        " BV_DRY_RUN inside the production wrapper"
                    ),
                    reason_code=ReasonCode.PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES.value,
                )
            )
            continue
        if all(_starts_with_any(s_name, _FORBIDDEN_CLASS_PREFIXES) for s_name, _f, _l in subs):
            sub_names = ", ".join(s_name for s_name, _f, _l in subs)
            violations.append(
                _Violation(
                    file=proto_file,
                    lineno=proto_lineno,
                    kind="protocol",
                    name=proto_name,
                    detail=(
                        f"public Protocol {proto_name!r} has only mock"
                        f" subclasses ({sub_names}); production code must"
                        " ship a real concrete subclass too (or, preferably,"
                        " retire the Protocol and call the real dependency"
                        " directly)"
                    ),
                    reason_code=ReasonCode.PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES.value,
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Public scanner + scorer.
# ---------------------------------------------------------------------------


def scan_paths(
    roots: Iterable[str | Path],
    *,
    self_allowlist: Iterable[str] = _SELF_ALLOWLIST,
) -> list[_Violation]:
    """Walk ``roots`` and return all discipline-rule-15 violations."""

    scans: list[_FileScan] = []
    allowset = frozenset(self_allowlist)
    for root in roots:
        path = Path(root)
        if not path.exists():
            continue
        if path.is_file():
            candidates = [path] if path.suffix == ".py" else []
        else:
            candidates = sorted(path.rglob("*.py"))
        for py in candidates:
            rel = py.as_posix()
            if any(rel.endswith(allow) for allow in allowset):
                continue
            try:
                source = py.read_text()
            except OSError:
                continue
            scans.append(_scan_one_file(rel_path=rel, source=source))

    violations: list[_Violation] = []
    for scan in scans:
        violations.extend(scan.violations)
    violations.extend(_check_protocol_subclass_seam(scans=scans))
    return violations


def no_mock_or_fake_implementations(
    *,
    roots: Iterable[str | Path] | None = None,
    soft_warn_files: Iterable[str] | None = None,
) -> ScorerResult:
    """Run the static scan; return a ``ScorerResult``.

    Args:
        roots: Production roots to scan. Defaults to
            ``src/brickvision`` + ``src/brickvision_runtime``.
        soft_warn_files: Files whose violations are downgraded to
            warnings (recorded in ``details["soft_warnings"]`` but
            don't fail the scorer). Defaults to the v0.7.7-cascade
            allowance enumerated in
            ``docs/24-pending-tasks-tracker.md`` §24.3 (13 files).
            Pass an empty iterable to enforce strictly.
    """

    effective_roots: tuple[str | Path, ...] = (
        tuple(roots) if roots is not None else _DEFAULT_ROOTS
    )
    effective_soft = frozenset(
        soft_warn_files if soft_warn_files is not None else _V077_CASCADE_SOFT_WARN_FILES
    )

    raw = scan_paths(effective_roots)

    hard: list[_Violation] = []
    soft: list[_Violation] = []
    for v in raw:
        if v.file in effective_soft:
            soft.append(v)
        else:
            hard.append(v)

    details: dict[str, object] = {
        "violations": [_to_dict(v) for v in hard],
        "soft_warnings": [_to_dict(v) for v in soft],
        "roots": [str(r) for r in effective_roots],
        "soft_warn_files": sorted(effective_soft),
    }

    if hard:
        reason_codes = tuple(sorted({v.reason_code for v in hard}))
        return ScorerResult(score=0.0, reason_codes=reason_codes, details=details)
    return ScorerResult(score=1.0, reason_codes=(), details=details)


# Register so the harness can dispatch via the (skill_id, name) registry.
register_scorer(skill_id="meta:discipline-rule-15", name="NoMockOrFakeImplementations")(
    no_mock_or_fake_implementations
)


__all__ = [
    "no_mock_or_fake_implementations",
    "scan_paths",
]
