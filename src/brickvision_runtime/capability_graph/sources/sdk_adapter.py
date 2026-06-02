"""Source 1 of 5 — ``databricks-sdk`` Python AST walker (authority 1.0).

Per ``docs/23-databricks-capability-graph.md`` §23.1.1, the SDK is the
**structural backbone** of the capability graph: every other source
attaches edges to entities derived primarily from the SDK. Authority
weight is fixed at 1.0 (the highest source authority).

What this module does
=====================

Walks ``<sdk_root>/databricks/sdk/service/*.py`` via the standard-
library :mod:`ast` module (zero runtime imports of ``databricks.sdk``,
so the walker is deterministic, side-effect free, and runs offline
against a fixture even when the SDK is not installed in the environment).

Emits four entity kinds (per §23.1.1's "Output node kinds"):

  * :class:`SDKModuleEntity`   — one per ``databricks/sdk/service/*.py``
                                  file (excluding ``_internal``).
  * :class:`SDKServiceEntity`  — one per ``class XxxAPI:`` definition.
  * :class:`SDKMethodEntity`   — one per public method on a service class
                                  (skips dunders + leading-underscore
                                  methods).
  * :class:`SDKParseError`     — per-file when AST parse raises (e.g.,
                                  the file is unreadable, syntax-broken,
                                  or trips a Python-version mismatch).

Effect-class assignment (§23.1.1, §23.0.5)
==========================================

A deterministic verb-stem heuristic — the method's leading verb buckets
into:

  * ``read``           — get, list, describe, read, fetch, download,
                          enumerate, count, exists.
  * ``write``          — create, update, delete, put, post, patch, run,
                          start, stop, cancel, grant, revoke, set, add,
                          remove, install, uninstall, import_, export,
                          rename, transfer, share, unshare, restore,
                          rollback, refresh, sync, deploy, promote.
  * ``unclassified``   — anything that doesn't bucket; emits a
                          :data:`ReasonCode.CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN`
                          via the per-method ``effect_unclassified`` flag.
                          The graph_builder (later C.1 BULK step) raises
                          one ``Question`` per unclassified method for
                          human review.
  * ``write·hitl``     — NOT assigned at indexer time. The indexer flags
                          methods as ``write``; the policy layer
                          (``<BV_CATALOG>.<BV_SCHEMA>.production_aliases`` per
                          ``docs/16-identity-audit-replay.md`` §12.5) at
                          runtime upgrades to ``write·hitl`` if the
                          method's resource is in scope. This decoupling
                          keeps indexer authority (1.0) separate from
                          policy authority (which can drift per partner).

Step 2b extensions (this module)
================================

The following §23.1.1 enrichments are **now implemented** (step 2b):

  * ``sdk_dataclass`` enumeration — every top-level
    ``@dataclass``-decorated class becomes an :class:`SDKDataclassEntity`
    with stable ``dataclass_id = sdk:<module>.<ClassName>``.
  * ``sdk_field`` enumeration — every ``ast.AnnAssign`` field on a
    dataclass becomes an :class:`SDKFieldEntity` with
    ``field_id = sdk:<module>.<ClassName>.<field-name>``. Fields
    annotated with ``ClassVar[...]`` are excluded (they are class
    constants, not instance fields).
  * ``sdk_method.consumes`` cross-link — for each method, the type
    names referenced by its positional argument annotations (excluding
    ``self``) are resolved to dataclass ids in the same module first;
    cross-module names are left for ``graph_builder`` to resolve
    later. Result lands on
    :attr:`SDKMethodEntity.consumes_dataclass_ids`.
  * ``sdk_method.produces`` cross-link — same convention but for the
    return-annotation type names. Result lands on
    :attr:`SDKMethodEntity.produces_dataclass_ids`.
  * ``sdk_method.paginates`` peer detection — when a method name
    ends in ``_iter`` and its non-``_iter`` peer exists in the same
    service class, :attr:`SDKMethodEntity.paginates_method_id` is
    populated with the peer's method id. ``graph_builder`` uses this
    to suppress the duplicate ``_iter`` extension.
  * ``sdk_method.deprecates`` successor extraction — when the
    docstring contains a ``Deprecated`` admonition with a successor
    reference (``Use`` ``X`` ``instead``, ``Replaced by`` ``X``,
    or a ``.. deprecated::`` block followed by a recommended
    alternative), the parsed successor name is resolved against the
    same service class's methods.
    :attr:`SDKMethodEntity.deprecates_method_id` carries the
    resolved peer id (or ``None`` if the name didn't resolve in the
    same class — graph_builder will retry cross-class).

The walker remains entirely AST-based with zero runtime imports of
``databricks.sdk``; type resolution is purely lexical (we match by
the bare class name as the SDK never aliases its own dataclasses).

Cross-source linkage (left to graph_builder)
============================================

This module does not assign meta-skills or extensions. The mapping from
SDK service module → meta-skill is the graph_builder's job (it merges
across all 5 sources before assignment, with the
``docs_section_aliases`` table from §23.2.7 as the tiebreaker for
fragmented modules like ``settings``, ``iam``, ``workspace``).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import re
from collections.abc import Iterable
from pathlib import Path


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SDKMethodEntity:
    """One public method on a service class.

    ``method_id`` is the canonical capability-graph identifier:
    ``sdk:<module>.<service-class>.<method>``, e.g.,
    ``sdk:catalog.TablesAPI.create``.

    Step 2b cross-link fields
    -------------------------

    * :attr:`consumes_dataclass_ids` — dataclass ids referenced by the
      method's positional argument annotations (resolved to same-
      module dataclasses; cross-module names land in
      :attr:`consumes_unresolved_typenames` for graph_builder).
    * :attr:`produces_dataclass_ids` — dataclass ids referenced by
      the method's return-type annotation (same resolution rules as
      consumes).
    * :attr:`consumes_unresolved_typenames` /
      :attr:`produces_unresolved_typenames` — bare type names that
      did not match a same-module dataclass. These are kept verbatim
      so graph_builder can attempt cross-module resolution after
      merging all 5 source adapters.
    * :attr:`paginates_method_id` — when this method name ends in
      ``_iter`` and the non-iter peer exists in the same service
      class, the peer's ``method_id``; else ``None``.
    * :attr:`deprecates_method_id` — when the docstring's
      ``Deprecated`` admonition references a successor that resolves
      to another method on the same service class, the successor's
      ``method_id``; else ``None``.
    """

    method_id: str
    module_name: str
    service_class_name: str
    method_name: str
    signature: str
    docstring: str | None
    effect_class: str  # read | write | unclassified
    effect_verb_matched: str | None
    deprecated_in_docstring: bool
    source_file: str  # relative to sdk_root, posix-separated
    source_line: int
    content_hash: str  # sha256[:16] of (signature + docstring + effect_class)

    # Step 2b cross-link enrichments. Defaults preserve back-compat
    # with any caller that constructs SDKMethodEntity directly via the
    # legacy 12-field shape (e.g., older fixtures pre-step-2b).
    consumes_dataclass_ids: tuple[str, ...] = ()
    produces_dataclass_ids: tuple[str, ...] = ()
    consumes_unresolved_typenames: tuple[str, ...] = ()
    produces_unresolved_typenames: tuple[str, ...] = ()
    paginates_method_id: str | None = None
    deprecates_method_id: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SDKServiceEntity:
    """One ``XxxAPI`` service class within an SDK module."""

    service_id: str  # sdk:<module>.<class>
    module_name: str
    class_name: str
    docstring: str | None
    method_ids: tuple[str, ...]  # ordered as appearing in source
    source_file: str
    source_line: int
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class SDKModuleEntity:
    """One sub-module under ``databricks.sdk.service``."""

    module_id: str  # sdk:<module>
    module_name: str
    service_ids: tuple[str, ...]
    source_file: str  # the module's __init__.py or single-file module
    content_hash: str  # merkle of constituent service hashes


@dataclasses.dataclass(frozen=True, slots=True)
class SDKFieldEntity:
    """One annotated field on an SDK ``@dataclass``-decorated class.

    ``field_id`` is ``sdk:<module>.<ClassName>.<field-name>``. Fields
    annotated with ``ClassVar[...]`` are excluded by the walker (they
    are class constants, not instance fields).
    """

    field_id: str
    dataclass_id: str
    module_name: str
    class_name: str
    field_name: str
    type_str: str  # ast.unparse(annotation), deterministic
    optional: bool  # True if Optional[...] / X | None / has default
    default_str: str | None  # str-rendered default (None if no default)
    source_line: int
    content_hash: str  # sha256[:16] of (field_name, type_str, default_str)


@dataclasses.dataclass(frozen=True, slots=True)
class SDKDataclassEntity:
    """One ``@dataclass``-decorated typed class within an SDK module.

    Per §23.1.1 the SDK exposes ~1,373 such dataclasses; graph_builder
    cross-links each ``sdk_method`` to its consumed (request) and
    produced (response) dataclasses via :attr:`field_ids` referenced by
    method signatures.
    """

    dataclass_id: str  # sdk:<module>.<ClassName>
    module_name: str
    class_name: str
    docstring: str | None
    field_ids: tuple[str, ...]  # ordered as declared in source
    source_file: str
    source_line: int
    content_hash: str  # merkle of (class_name, docstring, field hashes)


@dataclasses.dataclass(frozen=True, slots=True)
class SDKParseError:
    """Per-file parse failure; the snapshot ships partial."""

    source_file: str
    error_kind: str  # SyntaxError | OSError | UnicodeDecodeError | ValueError
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class SDKAdapterResult:
    """Aggregate output of one SDK walk."""

    sdk_version: str | None  # parsed from databricks/sdk/version.py if present
    sdk_root: str  # absolute path the walker rooted at
    parsed_at_ms: int
    modules: tuple[SDKModuleEntity, ...]
    services: tuple[SDKServiceEntity, ...]
    methods: tuple[SDKMethodEntity, ...]
    parse_errors: tuple[SDKParseError, ...]
    # Step 2b additions. Defaults to empty tuples to preserve back-
    # compat with any older code that constructs SDKAdapterResult by
    # the pre-step-2b shape (only one in-tree caller does this — the
    # legacy fixture builder in graph_builder tests).
    dataclasses: tuple[SDKDataclassEntity, ...] = ()
    fields: tuple[SDKFieldEntity, ...] = ()


# ---------------------------------------------------------------------------
# Verb-stem effect-class heuristic
# ---------------------------------------------------------------------------


_READ_VERBS: frozenset[str] = frozenset(
    {
        "get",
        "list",
        "describe",
        "read",
        "fetch",
        "download",
        "enumerate",
        "count",
        "exists",
        "has",
        "is",
        "show",
        "search",
        "find",
        "lookup",
        "query",
    }
)

_WRITE_VERBS: frozenset[str] = frozenset(
    {
        "create",
        "update",
        "delete",
        "put",
        "post",
        "patch",
        "run",
        "start",
        "stop",
        "cancel",
        "grant",
        "revoke",
        "set",
        "add",
        "remove",
        "install",
        "uninstall",
        "import",  # method names like ``import_users``
        "export",
        "rename",
        "transfer",
        "share",
        "unshare",
        "restore",
        "rollback",
        "refresh",
        "sync",
        "deploy",
        "promote",
        "submit",
        "trigger",
        "send",
        "reset",
        "rotate",
        "renew",
        "publish",
        "unpublish",
        "register",
        "unregister",
        "assign",
        "unassign",
        "attach",
        "detach",
        "enable",
        "disable",
        "lock",
        "unlock",
        "purge",
    }
)


def classify_effect(method_name: str) -> tuple[str, str | None]:
    """Apply the §23.1.1 verb-stem heuristic.

    Returns ``(effect_class, matched_verb)``. ``effect_class`` is
    ``read`` | ``write`` | ``unclassified``. ``matched_verb`` is the
    actual verb stem the heuristic matched against (or ``None`` for
    unclassified — useful for the ``CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN``
    Question's evidence span).

    The heuristic strips one trailing underscore (so ``import_`` matches
    ``import``) and tokenizes on the first underscore (so
    ``get_or_create`` matches ``get`` and is correctly classified as
    ``read`` because the leading verb is read-only — this is the
    SDK's actual idiom for retrieve-with-fallback methods).
    """

    name = method_name.strip()
    if not name:
        return ("unclassified", None)

    # Strip trailing underscore: ``import_`` -> ``import``
    if name.endswith("_") and not name.endswith("__"):
        name = name[:-1]

    # Take leading verb token (everything up to the first underscore).
    verb = name.split("_", 1)[0].lower()

    if verb in _READ_VERBS:
        return ("read", verb)
    if verb in _WRITE_VERBS:
        return ("write", verb)
    return ("unclassified", None)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


_SERVICE_CLASS_SUFFIX_RE = re.compile(r"^[A-Z][A-Za-z0-9]*API$")
"""A service class is one whose name matches ``^[A-Z]...API$`` per the
databricks-sdk auto-generation convention. Negative match excludes
``HTTPError`` etc."""

_DEPRECATED_DOCSTRING_RE = re.compile(
    r"\.\.\s+deprecated\b|deprecated::|\b(?:DEPRECATED|Deprecated)\b"
)


def _is_service_class(name: str) -> bool:
    return bool(_SERVICE_CLASS_SUFFIX_RE.match(name))


def _content_hash(*parts: str | None) -> str:
    """Stable sha256[:16] of joined parts."""

    h = hashlib.sha256()
    for p in parts:
        if p is None:
            h.update(b"\x00")
        else:
            h.update(p.encode("utf-8"))
            h.update(b"\x00")
    return h.hexdigest()[:16]


def _render_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Pretty-print a function signature deterministically.

    Uses :func:`ast.unparse` if available (Python 3.9+) to get the
    annotated argument list back. Falls back to a manual rendering for
    older Pythons (we target 3.10+ so ``unparse`` is always present, but
    the fallback keeps the function single-pass-safe).
    """

    try:
        # ``unparse`` on the args + returns is the cleanest deterministic
        # rendering. We prepend the def name explicitly.
        args_src = ast.unparse(node.args)
        ret_src = (
            f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
        )
        return f"def {node.name}({args_src}){ret_src}"
    except Exception:
        # Fallback: minimal positional-arg rendering.
        names = [a.arg for a in node.args.args]
        return f"def {node.name}({', '.join(names)})"


def _docstring(node: ast.AST) -> str | None:
    """Return the function/class docstring or None."""

    return ast.get_docstring(node, clean=True)


def _public_method_nodes(
    cls: ast.ClassDef,
) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield public method-defs declared directly on ``cls``.

    Skips dunders (``__init__``, ``__repr__``, …) and leading-
    underscore privates. Does NOT recurse into nested classes (the
    databricks-sdk does not nest service classes; if it did, the
    convention would change and we'd revisit).
    """

    for stmt in cls.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = stmt.name
        if name.startswith("__") or name.startswith("_"):
            continue
        yield stmt


# ---------------------------------------------------------------------------
# Step 2b — dataclass + cross-link helpers
# ---------------------------------------------------------------------------


def _is_dataclass_decorated(cls: ast.ClassDef) -> bool:
    """True if ``cls`` is decorated with ``@dataclass`` /
    ``@dataclasses.dataclass`` (with or without parens / arguments).

    Recognises every form the databricks-sdk uses:

      * ``@dataclass``
      * ``@dataclass()``
      * ``@dataclass(frozen=True, slots=True)``
      * ``@dataclasses.dataclass``
      * ``@dataclasses.dataclass(...)``

    Aliased imports (``from dataclasses import dataclass as _dc``) are
    NOT recognised because the SDK never aliases ``dataclass``. If that
    changes upstream, the loop below can be extended to consult the
    file-level alias map.
    """

    for dec in cls.decorator_list:
        # Bare-name decorator: @dataclass or @some_other_name
        if isinstance(dec, ast.Name) and dec.id == "dataclass":
            return True
        # Call decorator: @dataclass(...) or @dataclasses.dataclass(...)
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id == "dataclass":
                return True
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "dataclass"
                and isinstance(func.value, ast.Name)
                and func.value.id == "dataclasses"
            ):
                return True
        # Attribute decorator: @dataclasses.dataclass
        if (
            isinstance(dec, ast.Attribute)
            and dec.attr == "dataclass"
            and isinstance(dec.value, ast.Name)
            and dec.value.id == "dataclasses"
        ):
            return True
    return False


def _is_classvar(annotation: ast.expr) -> bool:
    """True if ``annotation`` is ``ClassVar[...]`` (any quoting form).

    Drops:

      * ``ClassVar[int]``
      * ``typing.ClassVar[int]``
      * ``ClassVar``  (bare, with no subscript)
      * ``typing.ClassVar``
    """

    target = annotation
    if isinstance(target, ast.Subscript):
        target = target.value
    if isinstance(target, ast.Name) and target.id == "ClassVar":
        return True
    if (
        isinstance(target, ast.Attribute)
        and target.attr == "ClassVar"
        and isinstance(target.value, ast.Name)
        and target.value.id == "typing"
    ):
        return True
    return False


def _render_annotation(node: ast.expr) -> str:
    """Best-effort string rendering of a type annotation.

    Falls back to ``ast.dump`` when ``ast.unparse`` raises; the
    fallback is intentionally not parseable but is deterministic so
    content_hash stays stable across re-walks.
    """

    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node, annotate_fields=False)


def _is_optional_annotation(annotation: ast.expr) -> bool:
    """True if the annotation is ``Optional[X]``, ``typing.Optional[X]``,
    ``Union[..., None]`` / ``typing.Union[..., None]`` or ``X | None``.

    The walker uses this as one half of the ``optional`` flag on
    :class:`SDKFieldEntity`; the other half is "has a default value".
    """

    # Optional[X] / typing.Optional[X]
    if isinstance(annotation, ast.Subscript):
        head = annotation.value
        if isinstance(head, ast.Name) and head.id == "Optional":
            return True
        if (
            isinstance(head, ast.Attribute)
            and head.attr == "Optional"
            and isinstance(head.value, ast.Name)
            and head.value.id == "typing"
        ):
            return True
        # Union[X, None] / typing.Union[X, None]
        if (
            isinstance(head, ast.Name) and head.id == "Union"
        ) or (
            isinstance(head, ast.Attribute)
            and head.attr == "Union"
            and isinstance(head.value, ast.Name)
            and head.value.id == "typing"
        ):
            slice_node = annotation.slice
            members = (
                slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
            )
            for m in members:
                if isinstance(m, ast.Constant) and m.value is None:
                    return True
    # X | None  →  ast.BinOp(op=BitOr)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        for side in (annotation.left, annotation.right):
            if isinstance(side, ast.Constant) and side.value is None:
                return True
    return False


# Identifiers that are NOT user-defined Databricks dataclasses — we
# strip them when extracting referenced typenames so the cross-link
# resolver doesn't waste time on stdlib / typing wrappers.
_STDLIB_TYPING_NAMES: frozenset[str] = frozenset(
    {
        "Any", "AnyStr", "Awaitable", "Callable", "ClassVar", "Coroutine",
        "Dict", "Final", "FrozenSet", "Generator", "Generic", "Hashable",
        "Iterable", "Iterator", "List", "Literal", "Mapping", "MutableMapping",
        "MutableSequence", "MutableSet", "NoReturn", "Optional", "Sequence",
        "Set", "Tuple", "Type", "TypedDict", "Union",
        # Built-in container generics (PEP 585)
        "dict", "frozenset", "list", "set", "tuple", "type",
        # Built-in scalars + common stdlib types — these will never be
        # a Databricks dataclass id, so skip them up front.
        "bool", "bytes", "bytearray", "complex", "float", "int", "memoryview",
        "object", "range", "slice", "str", "None",
        # Async / IO-ish stdlib
        "datetime", "date", "time", "timedelta", "timezone", "Path", "IO",
        "BinaryIO", "TextIO",
        # Things the SDK uses heavily but aren't dataclasses
        "self", "cls",
    }
)


def _extract_referenced_typenames(node: ast.expr | None) -> tuple[str, ...]:
    """Collect bare class names referenced by an annotation.

    Walks ``node`` and yields any ``ast.Name.id`` (excluding stdlib
    + typing names) plus any ``ast.Attribute`` leaf identifiers
    (so ``catalog.CreateTableRequest`` contributes
    ``CreateTableRequest`` — but cross-module resolution is left to
    graph_builder).

    Order is preserved (depth-first) and duplicates removed (first
    occurrence wins) so output is stable across re-walks.
    """

    if node is None:
        return ()

    seen: dict[str, None] = {}

    def _walk(n: ast.expr) -> None:
        if isinstance(n, ast.Name):
            if n.id not in _STDLIB_TYPING_NAMES:
                seen.setdefault(n.id, None)
            return
        if isinstance(n, ast.Attribute):
            # Yield the leaf attribute (e.g., catalog.Table → "Table").
            if n.attr not in _STDLIB_TYPING_NAMES:
                seen.setdefault(n.attr, None)
            # Don't descend into n.value — it's the module path.
            return
        if isinstance(n, ast.Subscript):
            _walk(n.value)
            slice_node = n.slice
            if isinstance(slice_node, ast.Tuple):
                for el in slice_node.elts:
                    _walk(el)
            else:
                _walk(slice_node)
            return
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
            _walk(n.left)
            _walk(n.right)
            return
        if isinstance(n, ast.Tuple):
            for el in n.elts:
                _walk(el)
            return
        # ast.Constant (None / forward-refs as strings) — for forward
        # refs as string literals, don't try to re-parse; the SDK only
        # rarely uses them and we'd just need graph_builder to do this
        # work anyway.

    _walk(node)
    return tuple(seen.keys())


def _extract_dataclass_field_entities(
    *,
    cls: ast.ClassDef,
    module_name: str,
) -> tuple[tuple[SDKFieldEntity, ...], tuple[str, ...]]:
    """Walk a dataclass body, returning (field_entities, field_ids).

    Returns ordered tuples mirroring the source-order declaration.
    """

    fields: list[SDKFieldEntity] = []
    field_ids: list[str] = []

    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name):
            continue  # Skip ``self.x: int = 0`` style on methods.
        if _is_classvar(stmt.annotation):
            continue
        field_name = stmt.target.id
        if field_name.startswith("__"):
            continue  # Defensive: dataclass dunder fields
        type_str = _render_annotation(stmt.annotation)
        is_optional = _is_optional_annotation(stmt.annotation) or stmt.value is not None
        default_str = (
            _render_annotation(stmt.value) if stmt.value is not None else None
        )
        field_id = f"sdk:{module_name}.{cls.name}.{field_name}"
        fields.append(
            SDKFieldEntity(
                field_id=field_id,
                dataclass_id=f"sdk:{module_name}.{cls.name}",
                module_name=module_name,
                class_name=cls.name,
                field_name=field_name,
                type_str=type_str,
                optional=is_optional,
                default_str=default_str,
                source_line=stmt.lineno,
                content_hash=_content_hash(field_name, type_str, default_str),
            )
        )
        field_ids.append(field_id)

    return tuple(fields), tuple(field_ids)


_DEPRECATION_SUCCESSOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ``Use ``new_method`` instead`` — most common SDK form
    re.compile(r"[Uu]se\s+``([A-Za-z_][\w\.]*)``\s+instead", re.MULTILINE),
    # ``Replaced by ``new_method`` `` / ``Use ``new_method``.`` (no "instead")
    re.compile(r"[Rr]eplaced\s+by\s+``([A-Za-z_][\w\.]*)``", re.MULTILINE),
    # ``.. deprecated::`` followed by recommendation. We capture the
    # first ``identifier`` that appears in the admonition body.
    re.compile(
        r"\.\.\s+deprecated::[^\n]*\n(?:[^\n]+\n)*?\s*[^\n]*?``([A-Za-z_][\w\.]*)``",
        re.MULTILINE,
    ),
    # Sphinx :meth: cross-ref form: ``:meth:`~MyAPI.new_method` ``
    re.compile(r":meth:`~?([A-Za-z_][\w\.]*)`", re.MULTILINE),
)


def _extract_deprecation_successor_name(docstring: str | None) -> str | None:
    """Try to extract the successor method's name from a ``Deprecated``
    docstring admonition.

    Returns the bare or dotted identifier the docstring recommends, or
    ``None`` if no recognised pattern matches. The returned value is a
    SOURCE-LEXICAL identifier — same-class resolution to a
    ``method_id`` happens in :func:`parse_sdk` post-walk.
    """

    if not docstring:
        return None
    if not _DEPRECATED_DOCSTRING_RE.search(docstring):
        return None
    for pat in _DEPRECATION_SUCCESSOR_PATTERNS:
        m = pat.search(docstring)
        if m:
            return m.group(1).strip()
    return None


def _is_pagination_iter(method_name: str) -> bool:
    """True if ``method_name`` ends in ``_iter``."""

    return bool(method_name) and method_name.endswith("_iter")


def _pagination_peer_name(method_name: str) -> str:
    """Strip the ``_iter`` suffix to derive the non-iter peer's name."""

    return method_name[: -len("_iter")] if _is_pagination_iter(method_name) else method_name


# ---------------------------------------------------------------------------
# Per-file parser
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _MethodPreview:
    """Internal: a method entity *without* cross-link resolution.

    Cross-links (``consumes_dataclass_ids`` /
    ``produces_dataclass_ids`` / ``paginates_method_id`` /
    ``deprecates_method_id``) are resolved in a second pass once
    every dataclass + every same-class method is known.
    """

    method_id: str
    module_name: str
    service_class_name: str
    method_name: str
    signature: str
    docstring: str | None
    effect_class: str
    effect_verb_matched: str | None
    deprecated_in_docstring: bool
    source_file: str
    source_line: int
    consumes_typenames: tuple[str, ...]
    produces_typenames: tuple[str, ...]
    deprecation_successor_name: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class _FileParse:
    """Internal: result of parsing one ``service/<x>.py`` file."""

    module_name: str
    rel_path: str
    services: tuple[SDKServiceEntity, ...]
    method_previews: tuple[_MethodPreview, ...]
    dataclasses: tuple[SDKDataclassEntity, ...]
    fields: tuple[SDKFieldEntity, ...]


def _walk_method_typenames(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(consumes_typenames, produces_typenames)`` for a method.

    ``consumes`` walks every positional argument annotation
    (``args.args`` + ``args.posonlyargs`` + ``args.kwonlyargs``)
    excluding ``self`` / ``cls``. ``produces`` walks the return
    annotation. Stdlib + typing wrappers are stripped by
    :func:`_extract_referenced_typenames`.
    """

    consumes: dict[str, None] = {}
    args = fn.args
    all_args: list[ast.arg] = []
    all_args.extend(args.posonlyargs)
    all_args.extend(args.args)
    all_args.extend(args.kwonlyargs)
    if args.vararg is not None:
        all_args.append(args.vararg)
    if args.kwarg is not None:
        all_args.append(args.kwarg)
    for a in all_args:
        if a.arg in ("self", "cls"):
            continue
        if a.annotation is None:
            continue
        for name in _extract_referenced_typenames(a.annotation):
            consumes.setdefault(name, None)

    produces = _extract_referenced_typenames(fn.returns)
    return tuple(consumes.keys()), produces


def _parse_one_file(
    *,
    path: Path,
    rel_path: str,
    module_name: str,
) -> _FileParse | SDKParseError:
    """Parse a single SDK service file. Returns either a successful
    ``_FileParse`` or a typed ``SDKParseError`` on any IO/syntax issue.
    """

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return SDKParseError(
            source_file=rel_path,
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return SDKParseError(
            source_file=rel_path,
            error_kind="SyntaxError",
            error_message=f"line={exc.lineno} {exc.msg}",
        )

    services: list[SDKServiceEntity] = []
    method_previews: list[_MethodPreview] = []
    dcs: list[SDKDataclassEntity] = []
    all_fields: list[SDKFieldEntity] = []

    # Walk top-level class defs only — service classes + dataclasses
    # always live at module top level in the databricks-sdk.
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        # Step 2b: emit dataclass + field entities BEFORE checking the
        # service-class branch so a class that's BOTH (rare but
        # possible) is captured on both sides. In practice the SDK
        # never decorates ``XxxAPI`` service classes with
        # ``@dataclass`` so the two branches are disjoint.
        if _is_dataclass_decorated(node):
            field_ents, field_ids = _extract_dataclass_field_entities(
                cls=node, module_name=module_name
            )
            class_doc = _docstring(node)
            dataclass_id = f"sdk:{module_name}.{node.name}"
            dcs.append(
                SDKDataclassEntity(
                    dataclass_id=dataclass_id,
                    module_name=module_name,
                    class_name=node.name,
                    docstring=class_doc,
                    field_ids=field_ids,
                    source_file=rel_path,
                    source_line=node.lineno,
                    content_hash=_content_hash(
                        node.name,
                        class_doc,
                        *(f.content_hash for f in field_ents),
                    ),
                )
            )
            all_fields.extend(field_ents)
            # Continue the loop — dataclasses don't contribute service
            # entities even if name happens to end in "API".
            continue

        if not _is_service_class(node.name):
            continue

        class_doc = _docstring(node)
        method_ids: list[str] = []

        for fn in _public_method_nodes(node):
            method_id = f"sdk:{module_name}.{node.name}.{fn.name}"
            method_ids.append(method_id)

            sig = _render_signature(fn)
            doc = _docstring(fn)
            effect, matched = classify_effect(fn.name)
            deprecated = bool(doc and _DEPRECATED_DOCSTRING_RE.search(doc))
            consumes_names, produces_names = _walk_method_typenames(fn)
            successor_name = _extract_deprecation_successor_name(doc)

            method_previews.append(
                _MethodPreview(
                    method_id=method_id,
                    module_name=module_name,
                    service_class_name=node.name,
                    method_name=fn.name,
                    signature=sig,
                    docstring=doc,
                    effect_class=effect,
                    effect_verb_matched=matched,
                    deprecated_in_docstring=deprecated,
                    source_file=rel_path,
                    source_line=fn.lineno,
                    consumes_typenames=consumes_names,
                    produces_typenames=produces_names,
                    deprecation_successor_name=successor_name,
                )
            )

        service_id = f"sdk:{module_name}.{node.name}"
        services.append(
            SDKServiceEntity(
                service_id=service_id,
                module_name=module_name,
                class_name=node.name,
                docstring=class_doc,
                method_ids=tuple(method_ids),
                source_file=rel_path,
                source_line=node.lineno,
                content_hash=_content_hash(
                    node.name, class_doc, *method_ids
                ),
            )
        )

    return _FileParse(
        module_name=module_name,
        rel_path=rel_path,
        services=tuple(services),
        method_previews=tuple(method_previews),
        dataclasses=tuple(dcs),
        fields=tuple(all_fields),
    )


# ---------------------------------------------------------------------------
# Top-level walker
# ---------------------------------------------------------------------------


def _resolve_service_dir(sdk_root: Path) -> Path | None:
    """Return ``<sdk_root>/databricks/sdk/service`` if it exists; else None.

    Accepts both:
      * an SDK package root (the directory containing
        ``databricks/sdk/...``), e.g., a venv site-packages location;
      * a path that already points at ``.../databricks/sdk/service``,
        for fixture testing.
    """

    candidate = sdk_root / "databricks" / "sdk" / "service"
    if candidate.is_dir():
        return candidate
    if (
        sdk_root.is_dir()
        and sdk_root.name == "service"
        and (sdk_root.parent.name == "sdk")
    ):
        return sdk_root
    return None


def _resolve_sdk_version(sdk_root: Path) -> str | None:
    """Best-effort: read ``databricks/sdk/version.py`` and grep
    ``__version__`` from its source without importing the SDK."""

    version_file = sdk_root / "databricks" / "sdk" / "version.py"
    if not version_file.is_file():
        return None
    try:
        src = version_file.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", src, re.MULTILINE)
    return m.group(1) if m else None


def _module_files(service_dir: Path) -> list[tuple[str, str, Path]]:
    """List ``(module_name, rel_path, abs_path)`` for every parseable
    file under ``service/``.

    Skips ``_internal``, ``__pycache__``, and any file whose stem
    starts with an underscore. Sorted deterministically.
    """

    out: list[tuple[str, str, Path]] = []
    for path in sorted(service_dir.rglob("*.py")):
        # Skip private sub-packages and bytecode caches.
        if "_internal" in path.parts:
            continue
        if "__pycache__" in path.parts:
            continue
        # Skip private top-level files (``_helpers.py``) but ALLOW
        # ``__init__.py`` of any sub-package — those are the canonical
        # entry point for SDK service packages like ``jobs/``,
        # ``compute/``, etc.
        if path.stem.startswith("_") and path.stem != "__init__":
            continue
        # Sub-package __init__.py at the service-dir root would mean
        # ``databricks/sdk/service/__init__.py`` itself (no module name);
        # skip that one because it's just the package marker.
        if path.stem == "__init__" and path.parent == service_dir:
            continue
        rel = path.relative_to(service_dir).as_posix()
        # Module name is the file stem unless this is a sub-package
        # ``__init__.py``, in which case the parent directory provides
        # the canonical module name.
        if path.stem == "__init__":
            module_name = path.parent.name
        else:
            module_name = path.stem
        out.append((module_name, f"databricks/sdk/service/{rel}", path))
    return out


def parse_sdk(
    *,
    sdk_root: Path | str,
    parsed_at_ms: int,
) -> SDKAdapterResult:
    """Top-level entry point — walk a databricks-sdk source tree.

    ``sdk_root`` should point at a directory whose layout is::

        sdk_root/
          databricks/
            sdk/
              version.py
              service/
                <module>.py
                ...

    The walker is purely AST-based and does **not** import any
    ``databricks.*`` module, so it works against:

      * an installed SDK (pass ``Path(databricks.sdk.__file__).parents[2]``)
      * a checked-out SDK repo (pass the repo root)
      * a synthetic fixture that mimics the layout (used by this
        module's tests)

    Returns an :class:`SDKAdapterResult` with module/service/method
    entities + per-file parse errors. Empty result with no errors ⇒ the
    SDK source tree was reachable but had zero ``*API`` classes (fail
    softly so callers can detect the surprising-empty case via
    ``CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED`` rather than throwing
    here).
    """

    root = Path(sdk_root).resolve()
    service_dir = _resolve_service_dir(root)
    sdk_version = _resolve_sdk_version(root)

    if service_dir is None:
        # Caller's responsibility: surface this as
        # CAPABILITY_GRAPH_SDK_PARSE_FAILED. We return an empty result
        # (with one parse_errors entry) rather than raising so the
        # adapter signature is total.
        return SDKAdapterResult(
            sdk_version=sdk_version,
            sdk_root=str(root),
            parsed_at_ms=parsed_at_ms,
            modules=(),
            services=(),
            methods=(),
            parse_errors=(
                SDKParseError(
                    source_file="<sdk_root>",
                    error_kind="ValueError",
                    error_message=(
                        f"could not locate databricks/sdk/service/ under {root}"
                    ),
                ),
            ),
        )

    files = _module_files(service_dir)

    # Group by module_name (sub-package modules can have multiple files).
    per_module_services: dict[str, list[SDKServiceEntity]] = {}
    per_module_files: dict[str, list[str]] = {}
    per_module_dataclass_names: dict[str, set[str]] = {}
    all_method_previews: list[_MethodPreview] = []
    all_dataclasses: list[SDKDataclassEntity] = []
    all_fields: list[SDKFieldEntity] = []
    parse_errors: list[SDKParseError] = []

    for module_name, rel_path, abs_path in files:
        result = _parse_one_file(
            path=abs_path,
            rel_path=rel_path,
            module_name=module_name,
        )
        if isinstance(result, SDKParseError):
            parse_errors.append(result)
            continue

        per_module_services.setdefault(module_name, []).extend(result.services)
        per_module_files.setdefault(module_name, []).append(rel_path)
        all_method_previews.extend(result.method_previews)
        all_dataclasses.extend(result.dataclasses)
        all_fields.extend(result.fields)
        # Track which dataclass names live in each module so the
        # cross-link resolver can answer "is name X a dataclass in
        # module Y?" in O(1).
        names = per_module_dataclass_names.setdefault(module_name, set())
        for dc in result.dataclasses:
            names.add(dc.class_name)

    # ---- Cross-link resolution (step 2b) -------------------------------
    #
    # 1. consumes_dataclass_ids / produces_dataclass_ids — resolve each
    #    typename to ``sdk:<module>.<TypeName>`` if the name lives in
    #    the same module; cross-module names are kept verbatim in
    #    ``*_unresolved_typenames`` for graph_builder to reconcile.
    # 2. paginates_method_id — resolve same-class peer for ``*_iter``.
    # 3. deprecates_method_id — resolve docstring successor against
    #    same-class methods first; cross-class names are dropped (the
    #    SDK convention is to recommend a same-class peer).
    # ---------------------------------------------------------------------

    # Index for same-class method lookup: (module, service_class) -> {name: method_id}.
    methods_by_class: dict[tuple[str, str], dict[str, str]] = {}
    for mp in all_method_previews:
        scope = (mp.module_name, mp.service_class_name)
        methods_by_class.setdefault(scope, {})[mp.method_name] = mp.method_id

    finalized: list[SDKMethodEntity] = []
    for mp in all_method_previews:
        scope = (mp.module_name, mp.service_class_name)
        # Same-module dataclass set (used for consumes/produces resolution).
        same_module_dcs = per_module_dataclass_names.get(mp.module_name, set())

        consumes_resolved: list[str] = []
        consumes_unresolved: list[str] = []
        for n in mp.consumes_typenames:
            if n in same_module_dcs:
                consumes_resolved.append(f"sdk:{mp.module_name}.{n}")
            else:
                consumes_unresolved.append(n)

        produces_resolved: list[str] = []
        produces_unresolved: list[str] = []
        for n in mp.produces_typenames:
            if n in same_module_dcs:
                produces_resolved.append(f"sdk:{mp.module_name}.{n}")
            else:
                produces_unresolved.append(n)

        # Pagination peer: only resolve if this method ends in _iter
        # AND the non-iter peer exists in the same class.
        paginates_method_id: str | None = None
        if _is_pagination_iter(mp.method_name):
            peer_name = _pagination_peer_name(mp.method_name)
            paginates_method_id = methods_by_class.get(scope, {}).get(peer_name)

        # Deprecation successor: resolve against same-class methods.
        deprecates_method_id: str | None = None
        succ = mp.deprecation_successor_name
        if succ:
            # Identifier may be ``new_method``, ``MyAPI.new_method``,
            # or ``module.MyAPI.new_method`` — take the LAST dotted
            # component as the method name and resolve in this class.
            short = succ.split(".")[-1]
            deprecates_method_id = methods_by_class.get(scope, {}).get(short)

        finalized.append(
            SDKMethodEntity(
                method_id=mp.method_id,
                module_name=mp.module_name,
                service_class_name=mp.service_class_name,
                method_name=mp.method_name,
                signature=mp.signature,
                docstring=mp.docstring,
                effect_class=mp.effect_class,
                effect_verb_matched=mp.effect_verb_matched,
                deprecated_in_docstring=mp.deprecated_in_docstring,
                source_file=mp.source_file,
                source_line=mp.source_line,
                content_hash=_content_hash(
                    mp.signature, mp.docstring, mp.effect_class
                ),
                consumes_dataclass_ids=tuple(consumes_resolved),
                produces_dataclass_ids=tuple(produces_resolved),
                consumes_unresolved_typenames=tuple(consumes_unresolved),
                produces_unresolved_typenames=tuple(produces_unresolved),
                paginates_method_id=paginates_method_id,
                deprecates_method_id=deprecates_method_id,
            )
        )

    # Build module entities (deterministic order: by module_name).
    modules: list[SDKModuleEntity] = []
    for module_name in sorted(per_module_services.keys()):
        services_in_mod = per_module_services[module_name]
        files_in_mod = per_module_files[module_name]
        service_ids = tuple(s.service_id for s in services_in_mod)
        modules.append(
            SDKModuleEntity(
                module_id=f"sdk:{module_name}",
                module_name=module_name,
                service_ids=service_ids,
                source_file=files_in_mod[0],
                content_hash=_content_hash(
                    module_name,
                    *(s.content_hash for s in services_in_mod),
                ),
            )
        )

    # Flatten services (deterministic order: by service_id).
    all_services = tuple(
        sorted(
            (s for svcs in per_module_services.values() for s in svcs),
            key=lambda s: s.service_id,
        )
    )

    return SDKAdapterResult(
        sdk_version=sdk_version,
        sdk_root=str(root),
        parsed_at_ms=parsed_at_ms,
        modules=tuple(modules),
        services=all_services,
        methods=tuple(sorted(finalized, key=lambda m: m.method_id)),
        parse_errors=tuple(parse_errors),
        dataclasses=tuple(sorted(all_dataclasses, key=lambda d: d.dataclass_id)),
        # Sort fields by (dataclass_id, source_line) so the within-
        # dataclass declaration order is preserved deterministically.
        # Sorting by field_id would alphabetize and break source order.
        fields=tuple(sorted(all_fields, key=lambda f: (f.dataclass_id, f.source_line))),
    )


__all__ = [
    "SDKAdapterResult",
    "SDKDataclassEntity",
    "SDKFieldEntity",
    "SDKMethodEntity",
    "SDKModuleEntity",
    "SDKParseError",
    "SDKServiceEntity",
    "classify_effect",
    "parse_sdk",
]
