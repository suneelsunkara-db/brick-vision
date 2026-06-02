"""Source 5 of 5 — Databricks Labs / Lakebridge repo walker
(authority 0.75; per §23.1.6).

Per **directive 2 of the v0.7.7 design exchange**, Lakebridge is the
explicit corpus the indexer must read for the **Migration top-order
skill**. Reference:
``https://github.com/databrickslabs/lakebridge/tree/main/src/databricks/labs/lakebridge``

What this module does
=====================

Walks a checked-out Lakebridge repo and emits typed entities by
parsing each ``.py`` file's AST. **No I/O beyond filesystem reads** —
no clones, no fetches, no docstring-rendering subprocesses. The
indexer's ``extract_labs`` task is responsible for cloning Lakebridge
to a local path (``${BV_INDEXER_LAKEBRIDGE_ROOT}``); this adapter
operates strictly on that already-checked-out tree.

Why a SEPARATE adapter from sdk_adapter
=======================================

Although both are AST walkers over Databricks Python codebases, they
differ structurally:

  1. **Different organizing principle.** SDK is API-facing — every
     public surface is a ``XxxAPI`` service class with method
     verbs. Lakebridge is workflow-facing — public surfaces are CLI
     commands, transpiler engines, and assessment runners organized
     by migration phase (assess → analyze → transpile → validate).
     The "service" abstraction doesn't apply.
  2. **Different verb vocabulary.** SDK's read/write effect-class
     vocabulary doesn't fit Lakebridge — every Lakebridge function
     is technically a "read" (it never writes to user data, only to
     local artifacts). The skill-bearing axis is instead **migration
     phase**: ``assessment | analysis | transpile | validation |
     unknown``.
  3. **CLI decorator detection.** Lakebridge's entry-point surface is
     defined by ``@click.command`` / ``@app.command`` /
     ``@cli.command`` decorators. The SDK has no such concept.
  4. **Single top-order alignment.** All Lakebridge entities feed the
     **Migration** top-order skill exclusively (graph_builder asserts
     this); SDK entities fan out across multiple top-orders.

Reused from sdk_adapter
=======================

  * :func:`sdk_adapter._content_hash` — stable sha256[:16] hashing.

Cross-package private import (the leading-underscore reach-around)
is intentional — both adapters are siblings in the
``brickvision_runtime.capability_graph.sources`` package, and the hash
function is package-internal. Refactor to a shared
``_helpers.py`` module is a v0.7.8 task.

Entity model
============

  * :class:`LabsRepoEntity`     — top-level repo container (1 per run).
  * :class:`LabsModuleEntity`   — one per ``.py`` file under the
                                   walked tree (post leading-underscore
                                   filter and test-dir filter).
  * :class:`LabsClassEntity`    — one per public class (covers config
                                   dataclasses, transpiler engines,
                                   assessment runners, etc.).
  * :class:`LabsCallableEntity` — one per public function or method.
                                   Carries ``is_cli_command`` (decorator
                                   detection) + ``category``
                                   (migration-phase verb-stem heuristic).

Reason codes
============

Per §23.1.6:
  * :data:`ReasonCode.CAPABILITY_GRAPH_LABS_FETCH_FAILED` — emitted by
    the indexer's ``extract_labs`` task on git-clone error, NOT by
    this adapter.
  * :data:`ReasonCode.CAPABILITY_GRAPH_LABS_PARSE_FAILED` — per-file,
    soft fail; surfaced via :class:`LabsParseError` in the result.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterable, Sequence
from pathlib import Path

from .sdk_adapter import _content_hash


# ---------------------------------------------------------------------------
# Migration-phase classification
# ---------------------------------------------------------------------------


_ASSESSMENT_VERBS: frozenset[str] = frozenset(
    {
        "assess",
        "scan",
        "probe",
        "discover",
        "inventory",
        "profile",
        "audit",
        "survey",
        "diagnose",
    }
)
"""Phase 1: identifying what's in the source system."""

_ANALYSIS_VERBS: frozenset[str] = frozenset(
    {
        "analyze",
        "analyse",
        "parse",
        "lint",
        "inspect",
        "interpret",
        "tokenize",
        "tokenise",
        "trace",
        "explain",
        "summarize",
        "summarise",
    }
)
"""Phase 2: understanding the source artifacts."""

_TRANSPILE_VERBS: frozenset[str] = frozenset(
    {
        "transpile",
        "translate",
        "convert",
        "rewrite",
        "transform",
        "render",
        "emit",
        "generate",
        "compile",
    }
)
"""Phase 3: producing target-system equivalents."""

_VALIDATION_VERBS: frozenset[str] = frozenset(
    {
        "validate",
        "verify",
        "compare",
        "diff",
        "reconcile",
        "match",
        "check",
        "assert",
        "test",
    }
)
"""Phase 4: confirming target equivalence."""

_PHASE_BY_VERB: dict[str, str] = {
    **{v: "assessment" for v in _ASSESSMENT_VERBS},
    **{v: "analysis" for v in _ANALYSIS_VERBS},
    **{v: "transpile" for v in _TRANSPILE_VERBS},
    **{v: "validation" for v in _VALIDATION_VERBS},
}


def classify_phase(name: str) -> str:
    """Return migration phase from the leading verb of a Python name.

    Returns one of ``assessment``, ``analysis``, ``transpile``,
    ``validation``, or ``unknown``.

    Lakebridge's organizing axis is the migration lifecycle; this
    heuristic gives graph_builder a coarse phase tag so retrieval
    can prefer phase-aligned helpers (e.g., when a user asks "validate
    transpiled tables", surface the `validation` callables first).

    Examples
    --------
    >>> classify_phase("assess_source_warehouse")
    'assessment'
    >>> classify_phase("transpile_query")
    'transpile'
    >>> classify_phase("compare_results")
    'validation'
    >>> classify_phase("Application")
    'unknown'
    """

    if not name:
        return "unknown"
    parts = name.split("_", 1)
    leading = parts[0].lower()
    return _PHASE_BY_VERB.get(leading, "unknown")


# ---------------------------------------------------------------------------
# CLI decorator detection
# ---------------------------------------------------------------------------


_CLI_DECORATOR_LEAVES: frozenset[str] = frozenset(
    {
        "command",
        "group",
        "argument",
        "option",
    }
)
"""Last-segment names of decorators that mark a function as a CLI
command. Combined with these prefixes:

  * ``click.command`` → leaf=``command``
  * ``app.command`` (databricks-labs-blueprint) → leaf=``command``
  * ``cli.command`` → leaf=``command``
  * ``click.group``, ``app.group`` → leaf=``group``

We treat any decorator whose final attribute is in this set as
CLI-marking. Bare ``command`` (no namespace) is treated the same way
because some labs repos do ``from click import command`` and decorate
with ``@command``."""


def _decorator_leaf_name(node: ast.expr) -> str | None:
    """Return the rightmost name segment of a decorator expression.

    Handles:
      * ``@command``                 → ``"command"``
      * ``@click.command``           → ``"command"``
      * ``@app.command()``           → ``"command"``
      * ``@click.command("foo")``    → ``"command"``
      * ``@app.group("migrate")``    → ``"group"``

    Returns ``None`` for unrecognized shapes.
    """

    if isinstance(node, ast.Call):
        return _decorator_leaf_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_cli_command(decorator_list: list[ast.expr]) -> bool:
    """Return True iff any decorator marks the callable as a CLI entry."""

    for dec in decorator_list:
        leaf = _decorator_leaf_name(dec)
        if leaf in _CLI_DECORATOR_LEAVES:
            return True
    return False


def _is_dataclass(decorator_list: list[ast.expr]) -> bool:
    """Return True iff a class is decorated with ``@dataclass`` or
    ``@dataclasses.dataclass`` (with or without arguments)."""

    for dec in decorator_list:
        leaf = _decorator_leaf_name(dec)
        if leaf == "dataclass":
            return True
    return False


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class LabsCallableEntity:
    """One per public function or method."""

    callable_id: str  # e.g., "labs:lakebridge.transpiler:transpile_query"
    module_id: str
    class_id: str | None  # parent class id when this is a method
    name: str
    qualname: str  # e.g., "Transpiler.transpile_query" or "transpile_query"
    signature: str
    docstring: str | None
    phase: str  # assessment | analysis | transpile | validation | unknown
    is_cli_command: bool
    is_method: bool
    is_async: bool
    line_number: int
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class LabsClassEntity:
    """One per public class."""

    class_id: str  # e.g., "labs:lakebridge.config:TranspileConfig"
    module_id: str
    name: str
    is_dataclass: bool
    base_classes: tuple[str, ...]
    docstring: str | None
    line_number: int
    callable_ids: tuple[str, ...]  # member callables, source order
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class LabsModuleEntity:
    """One per ``.py`` file."""

    module_id: str  # e.g., "labs:lakebridge.transpiler"
    repo_id: str
    module_path: str  # repo-relative POSIX path
    docstring: str | None
    class_ids: tuple[str, ...]
    callable_ids: tuple[str, ...]  # module-level public callables only
    line_count: int
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class LabsRepoEntity:
    """Top-level repo container (1 per indexer run)."""

    repo_id: str  # "labs:lakebridge"
    repo_url: str
    repo_revision: str | None  # caller stamps when known (git rev-parse HEAD)
    module_count: int
    class_count: int
    callable_count: int
    cli_command_count: int
    phase_distribution: tuple[tuple[str, int], ...]  # (phase, count) sorted
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class LabsParseError:
    """Per-file parse failure; the snapshot ships partial."""

    module_path: str
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class LabsAdapterResult:
    """Aggregate output of one ``parse_lakebridge`` invocation."""

    parsed_at_ms: int
    repo: LabsRepoEntity | None  # None when zero modules walked
    modules: tuple[LabsModuleEntity, ...]
    classes: tuple[LabsClassEntity, ...]
    callables: tuple[LabsCallableEntity, ...]
    parse_errors: tuple[LabsParseError, ...]


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _render_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render a callable signature back to source-level text.

    Mirrors :func:`sdk_adapter._render_signature` but kept local so
    sibling adapters can diverge if Lakebridge requires different
    rendering (e.g., click-style ``@click.option`` annotation
    rendering). For now identical behavior.
    """

    parts: list[str] = []
    a = node.args

    pos_args = (a.posonlyargs or []) + a.args
    defaults = list(a.defaults)
    n_pos = len(pos_args)
    n_no_default = n_pos - len(defaults)

    posonly_count = len(a.posonlyargs or [])

    for i, arg in enumerate(pos_args):
        s = arg.arg
        if arg.annotation is not None:
            s += f": {ast.unparse(arg.annotation)}"
        if i >= n_no_default:
            d = defaults[i - n_no_default]
            s += f" = {ast.unparse(d)}"
        parts.append(s)
        if posonly_count and i == posonly_count - 1:
            parts.append("/")

    if a.vararg is not None:
        s = "*" + a.vararg.arg
        if a.vararg.annotation is not None:
            s += f": {ast.unparse(a.vararg.annotation)}"
        parts.append(s)
    elif a.kwonlyargs:
        parts.append("*")

    for kwarg, kwd in zip(a.kwonlyargs, a.kw_defaults):
        s = kwarg.arg
        if kwarg.annotation is not None:
            s += f": {ast.unparse(kwarg.annotation)}"
        if kwd is not None:
            s += f" = {ast.unparse(kwd)}"
        parts.append(s)

    if a.kwarg is not None:
        s = "**" + a.kwarg.arg
        if a.kwarg.annotation is not None:
            s += f": {ast.unparse(a.kwarg.annotation)}"
        parts.append(s)

    sig = "(" + ", ".join(parts) + ")"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    return sig


def _docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
) -> str | None:
    return ast.get_docstring(node, clean=True)


def _public_top_level_nodes(
    body: list[ast.stmt],
) -> tuple[list[ast.FunctionDef | ast.AsyncFunctionDef], list[ast.ClassDef]]:
    """Return (public_callables, public_classes) at module/class scope.

    Drops names with leading underscore (private convention). Keeps
    ``__init__`` for classes intentionally — ``__init__`` is the
    constructor and is the entry point for class-based callables.
    """

    callables: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    classes: list[ast.ClassDef] = []
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name.startswith("_") and stmt.name != "__init__":
                continue
            callables.append(stmt)
        elif isinstance(stmt, ast.ClassDef):
            if stmt.name.startswith("_"):
                continue
            classes.append(stmt)
    return callables, classes


# ---------------------------------------------------------------------------
# Path → module-id helpers
# ---------------------------------------------------------------------------


_LAKEBRIDGE_REL_PREFIX: tuple[str, ...] = ("src", "databricks", "labs", "lakebridge")
"""The path prefix under the repo root that contains the
top-level ``lakebridge`` package. The adapter strips this prefix
to compute module ids of the form ``labs:lakebridge.<rest>``."""


def _module_id_for_path(repo_root: Path, file_path: Path) -> str | None:
    """Convert a file path to a ``labs:lakebridge.<dotted>`` module id.

    Returns ``None`` if the file is outside the
    ``src/databricks/labs/lakebridge/`` tree (we don't emit entities
    for build helpers, conftest, etc., even if they're parseable).
    """

    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        return None

    rel_parts = rel.parts
    if rel_parts[: len(_LAKEBRIDGE_REL_PREFIX)] != _LAKEBRIDGE_REL_PREFIX:
        return None

    after_prefix = rel_parts[len(_LAKEBRIDGE_REL_PREFIX) :]
    if not after_prefix:
        return None

    if after_prefix[-1] == "__init__.py":
        dotted_parts = ("lakebridge",) + after_prefix[:-1]
    elif after_prefix[-1].endswith(".py"):
        dotted_parts = ("lakebridge",) + after_prefix[:-1] + (after_prefix[-1][:-3],)
    else:
        return None

    return "labs:" + ".".join(dotted_parts)


# ---------------------------------------------------------------------------
# Per-file parser
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _FileParse:
    """Internal: parsed module + its emitted entities."""

    module: LabsModuleEntity
    classes: tuple[LabsClassEntity, ...]
    callables: tuple[LabsCallableEntity, ...]


def _emit_callable(
    *,
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    module_id: str,
    class_id: str | None,
    parent_class_name: str | None,
) -> LabsCallableEntity:
    qualname = (
        f"{parent_class_name}.{fn.name}" if parent_class_name else fn.name
    )
    sig = _render_signature(fn)
    callable_id = f"{module_id}:{qualname}"
    return LabsCallableEntity(
        callable_id=callable_id,
        module_id=module_id,
        class_id=class_id,
        name=fn.name,
        qualname=qualname,
        signature=sig,
        docstring=_docstring(fn),
        phase=classify_phase(fn.name),
        is_cli_command=_is_cli_command(fn.decorator_list),
        is_method=class_id is not None,
        is_async=isinstance(fn, ast.AsyncFunctionDef),
        line_number=fn.lineno,
        content_hash=_content_hash(callable_id, sig, _docstring(fn)),
    )


def _emit_class(
    *,
    cls: ast.ClassDef,
    module_id: str,
) -> tuple[LabsClassEntity, list[LabsCallableEntity]]:
    class_id = f"{module_id}:{cls.name}"
    method_nodes, _nested_classes = _public_top_level_nodes(cls.body)
    method_entities = [
        _emit_callable(
            fn=fn,
            module_id=module_id,
            class_id=class_id,
            parent_class_name=cls.name,
        )
        for fn in method_nodes
    ]
    base_strs = tuple(ast.unparse(b) for b in cls.bases)
    cls_entity = LabsClassEntity(
        class_id=class_id,
        module_id=module_id,
        name=cls.name,
        is_dataclass=_is_dataclass(cls.decorator_list),
        base_classes=base_strs,
        docstring=_docstring(cls),
        line_number=cls.lineno,
        callable_ids=tuple(m.callable_id for m in method_entities),
        content_hash=_content_hash(
            class_id,
            *base_strs,
            _docstring(cls),
            *(m.content_hash for m in method_entities),
        ),
    )
    return cls_entity, method_entities


def _parse_one_file(
    *,
    repo_root: Path,
    file_path: Path,
    repo_id: str,
) -> _FileParse | LabsParseError:
    """Parse a single ``.py`` file → module + classes + callables."""

    module_id = _module_id_for_path(repo_root, file_path)
    if module_id is None:
        # Outside the tracked tree; caller shouldn't have reached here,
        # but defend defensively.
        return LabsParseError(
            module_path=str(file_path.relative_to(repo_root)),
            error_kind="ValueError",
            error_message="file is outside src/databricks/labs/lakebridge/",
        )

    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return LabsParseError(
            module_path=str(file_path.relative_to(repo_root)),
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        return LabsParseError(
            module_path=str(file_path.relative_to(repo_root)),
            error_kind="SyntaxError",
            error_message=f"{exc.msg} at line {exc.lineno}",
        )

    module_callables_ast, module_classes_ast = _public_top_level_nodes(tree.body)

    module_callables: list[LabsCallableEntity] = [
        _emit_callable(
            fn=fn, module_id=module_id, class_id=None, parent_class_name=None
        )
        for fn in module_callables_ast
    ]

    class_entities: list[LabsClassEntity] = []
    method_callables: list[LabsCallableEntity] = []
    for cls in module_classes_ast:
        cls_entity, method_entities = _emit_class(cls=cls, module_id=module_id)
        class_entities.append(cls_entity)
        method_callables.extend(method_entities)

    rel_path_posix = file_path.relative_to(repo_root).as_posix()
    line_count = source.count("\n") + (0 if source.endswith("\n") else 1)
    module = LabsModuleEntity(
        module_id=module_id,
        repo_id=repo_id,
        module_path=rel_path_posix,
        docstring=_docstring(tree),
        class_ids=tuple(c.class_id for c in class_entities),
        callable_ids=tuple(c.callable_id for c in module_callables),
        line_count=line_count,
        content_hash=_content_hash(
            module_id,
            rel_path_posix,
            _docstring(tree),
            *(c.content_hash for c in class_entities),
            *(c.content_hash for c in module_callables),
        ),
    )

    return _FileParse(
        module=module,
        classes=tuple(class_entities),
        callables=tuple(module_callables) + tuple(method_callables),
    )


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


_TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test", "__pycache__"})
"""Directory basenames we never descend into."""


def _module_files(repo_root: Path) -> Iterable[Path]:
    """Yield every ``.py`` file under ``<repo_root>/src/databricks/labs/lakebridge/``.

    Excludes:
      * Any directory whose basename is in :data:`_TEST_DIR_NAMES`.
      * Any directory whose basename starts with ``_`` (private), but
        permits ``__init__.py`` files in normal packages.
      * Any file whose basename starts with ``_`` other than
        ``__init__.py`` (e.g., ``_internal.py`` is private).
      * Any file matching ``test_*.py`` or ``*_test.py``.
    """

    pkg_root = repo_root.joinpath(*_LAKEBRIDGE_REL_PREFIX)
    if not pkg_root.is_dir():
        return ()

    files: list[Path] = []
    for path in sorted(pkg_root.rglob("*.py")):
        # Skip if any path segment is a test/cache dir
        rel = path.relative_to(repo_root)
        if any(part in _TEST_DIR_NAMES for part in rel.parts):
            continue

        # Skip if a non-leaf directory starts with underscore (private
        # package). The leaf file's underscore-handling is below.
        intermediate_parts = rel.parts[:-1]
        if any(part.startswith("_") for part in intermediate_parts):
            continue

        # Filename rules.
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            continue
        if name.startswith("_") and name != "__init__.py":
            continue

        files.append(path)
    return files


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


_LAKEBRIDGE_REPO_URL: str = (
    "https://github.com/databrickslabs/lakebridge"
)


def parse_lakebridge(
    *,
    repo_root: Path | str,
    parsed_at_ms: int,
    repo_revision: str | None = None,
) -> LabsAdapterResult:
    """Walk a checked-out Lakebridge repo and emit typed entities.

    Parameters
    ----------
    repo_root : Path | str
        Filesystem path to the repo's root (the directory containing
        ``src/databricks/labs/lakebridge/``).
    parsed_at_ms : int
        Wall-clock timestamp of the parse; the indexer pins this for
        replay.
    repo_revision : str | None
        Optional ``git rev-parse HEAD`` output the caller resolved
        before invoking; surfaced on the :class:`LabsRepoEntity` for
        reproducibility.

    Returns
    -------
    LabsAdapterResult
        Files that fail to parse are isolated to ``parse_errors``;
        sibling files continue. When zero modules are walked
        (``src/databricks/labs/lakebridge/`` missing or empty),
        ``repo`` is ``None``.
    """

    root = Path(repo_root).resolve()
    repo_id = "labs:lakebridge"

    modules: list[LabsModuleEntity] = []
    classes: list[LabsClassEntity] = []
    callables: list[LabsCallableEntity] = []
    parse_errors: list[LabsParseError] = []

    for file_path in _module_files(root):
        result = _parse_one_file(
            repo_root=root, file_path=file_path, repo_id=repo_id
        )
        if isinstance(result, LabsParseError):
            parse_errors.append(result)
            continue
        modules.append(result.module)
        classes.extend(result.classes)
        callables.extend(result.callables)

    modules.sort(key=lambda m: m.module_id)
    classes.sort(key=lambda c: c.class_id)
    callables.sort(key=lambda c: c.callable_id)

    repo: LabsRepoEntity | None = None
    if modules:
        # Aggregate stats.
        cli_count = sum(1 for c in callables if c.is_cli_command)
        phase_counts: dict[str, int] = {}
        for c in callables:
            phase_counts[c.phase] = phase_counts.get(c.phase, 0) + 1
        phase_distribution = tuple(sorted(phase_counts.items()))

        repo = LabsRepoEntity(
            repo_id=repo_id,
            repo_url=_LAKEBRIDGE_REPO_URL,
            repo_revision=repo_revision,
            module_count=len(modules),
            class_count=len(classes),
            callable_count=len(callables),
            cli_command_count=cli_count,
            phase_distribution=phase_distribution,
            content_hash=_content_hash(
                repo_id,
                repo_revision,
                str(len(modules)),
                str(len(classes)),
                str(len(callables)),
                *(m.content_hash for m in modules),
            ),
        )

    return LabsAdapterResult(
        parsed_at_ms=parsed_at_ms,
        repo=repo,
        modules=tuple(modules),
        classes=tuple(classes),
        callables=tuple(callables),
        parse_errors=tuple(parse_errors),
    )


__all__ = [
    "LabsAdapterResult",
    "LabsCallableEntity",
    "LabsClassEntity",
    "LabsModuleEntity",
    "LabsParseError",
    "LabsRepoEntity",
    "classify_phase",
    "parse_lakebridge",
]
