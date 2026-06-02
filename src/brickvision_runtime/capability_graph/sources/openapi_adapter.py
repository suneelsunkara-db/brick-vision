"""Source 2 of 5 — Databricks OpenAPI 3.x walker (authority 0.95).

Per ``docs/23-databricks-capability-graph.md`` §23.1.2, OpenAPI gives us
**effect-class evidence and per-method authentication / payload schemas
the Python SDK normalizes away**. Slightly below SDK in authority because
OpenAPI lags occasionally — empirically ~3-5 endpoints per quarter ship
in the SDK before they appear in OpenAPI.

What this module does
=====================

Walks pre-fetched OpenAPI 3.x documents (one per Databricks API version
— typically ``2.0``, ``2.1``, ``2.2``) and emits four entity kinds:

  * :class:`OpenAPIDocumentEntity`        — one per parsed document
                                             (carries info.title,
                                             info.version, server URLs).
  * :class:`OpenAPIOperationEntity`       — one per ``paths.<p>.<verb>``
                                             with operationId.
  * :class:`OpenAPISchemaEntity`          — one per ``components.schemas.<x>``.
  * :class:`OpenAPISecuritySchemeEntity`  — one per
                                             ``components.securitySchemes.<x>``.
  * :class:`OpenAPIParseError`            — per-document parse failure;
                                             other docs continue.

Hand-off contract
=================

The caller (the indexer's ``extract_openapi`` task; later C.1 BULK step
~5) is responsible for:

  * HTTPS GET against ``https://docs.databricks.com/api/<path>/openapi.json``
    (rate-limited 4 req/s, 3× retry);
  * Caching the body in
    ``<BV_CATALOG>.<BV_SCHEMA>.snapshots/openapi/<api-version>/`` for replay;
  * Passing the parsed JSON dict into :func:`parse_openapi`.

Keeping IO out of this adapter makes it side-effect free, deterministic,
and offline-testable against synthetic fixtures.

Cross-source linkage (left to graph_builder)
============================================

The keystone cross-link per §23.1.2 is:

    openapi_operation.implements → sdk_method     (~95% coverage)

This adapter does NOT itself resolve that edge — the SDK adapter
emits ``sdk_method`` entities under the ``sdk:`` namespace and this
module emits ``openapi:`` entities. The graph_builder (C.1 BULK step 7)
joins on the ``__databricks_path__`` class attribute (when present on
the SDK class) plus ``operationId``-vs-``method_name`` similarity. We
emit ``operation_id_raw`` + ``path`` + ``http_method`` so the join keys
are available; we do not pre-compute the join here because doing it
in-adapter would couple Source 2 to Source 1's parse order.

Effect-class refinement
=======================

Per §23.1.2: the OpenAPI ``x-databricks-effect-class`` extension (where
present, ~70% of operations) **overrides** the SDK adapter's verb-stem
classification. We capture the raw value in
:attr:`OpenAPIOperationEntity.effect_class_raw` and the normalized
value in :attr:`OpenAPIOperationEntity.effect_class`. The graph_builder
uses ``effect_class`` from the OpenAPI entity if non-None; otherwise
falls back to the SDK entity's heuristic value.

Reason codes
============

Per §23.1.2:
  * :data:`ReasonCode.CAPABILITY_GRAPH_OPENAPI_FETCH_FAILED` — emitted by
    the indexer's ``extract_openapi`` task on HTTP error, NOT by this
    adapter (this adapter doesn't fetch).
  * :data:`ReasonCode.CAPABILITY_GRAPH_OPENAPI_SDK_LINK_MISSING` — emitted
    by the graph_builder, NOT by this adapter (cross-link decision).

This adapter never raises ``ReasonCode``s itself; it returns
``OpenAPIParseError`` aggregates that the caller maps to reason codes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------


_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
"""OpenAPI 3.x verb keys recognised inside a path-item object. ``trace``
is excluded because Databricks doesn't ship trace operations."""


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPIOperationEntity:
    """One ``paths.<path>.<http-method>`` operation.

    ``operation_id`` is the canonical capability-graph identifier:
    ``openapi:<api-version>:<operationId>`` (or, for spec-bug operations
    that lack an operationId, ``openapi:<api-version>:<http-method>:<path-slug>``).
    """

    operation_id: str
    api_version: str
    operation_id_raw: str | None  # the actual operationId from the spec
    path: str
    http_method: str  # uppercased: GET | POST | PUT | PATCH | DELETE | HEAD | OPTIONS
    summary: str | None
    description: str | None
    effect_class_raw: str | None  # x-databricks-effect-class value if present
    effect_class: str | None  # normalized: read | write | unclassified | None
    request_schema_refs: tuple[str, ...]  # schema names referenced from requestBody
    response_schema_refs: tuple[str, ...]  # schema names referenced from responses
    security_scheme_refs: tuple[str, ...]  # security scheme names from operation.security
    deprecated: bool
    tags: tuple[str, ...]
    source_url: str  # the openapi doc URL, for provenance
    content_hash: str  # sha256[:16] of (path + method + operationId + effect_class)


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPISchemaEntity:
    """One ``components.schemas.<schema-name>`` entry."""

    schema_id: str  # openapi:<api-version>:schema:<schema-name>
    api_version: str
    schema_name: str
    schema_type: str | None  # object | string | array | …
    description: str | None
    property_names: tuple[str, ...]
    required_property_names: tuple[str, ...]
    source_url: str
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPISecuritySchemeEntity:
    """One ``components.securitySchemes.<scheme-name>`` entry."""

    scheme_id: str  # openapi:<api-version>:security:<scheme-name>
    api_version: str
    scheme_name: str
    scheme_type: str | None  # apiKey | http | oauth2 | openIdConnect
    scheme_subtype: str | None  # bearer (for http); password/clientCredentials (for oauth2); …
    description: str | None
    source_url: str
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPIDocumentEntity:
    """One parsed OpenAPI document (one API version)."""

    document_id: str  # openapi:<api-version>:doc
    api_version: str
    info_title: str | None
    info_version: str | None
    info_description: str | None
    server_urls: tuple[str, ...]
    operation_ids: tuple[str, ...]  # member operations, deterministic order
    schema_ids: tuple[str, ...]
    security_scheme_ids: tuple[str, ...]
    source_url: str
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPIParseError:
    """Per-document parse failure; the snapshot ships partial."""

    api_version: str
    source_url: str
    error_kind: str  # ValueError | KeyError | TypeError
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPIDocumentInput:
    """One document the caller hands in for parsing."""

    document: Mapping[str, Any]
    api_version: str
    source_url: str


@dataclasses.dataclass(frozen=True, slots=True)
class OpenAPIAdapterResult:
    """Aggregate output of one ``parse_openapi`` invocation."""

    parsed_at_ms: int
    documents: tuple[OpenAPIDocumentEntity, ...]
    operations: tuple[OpenAPIOperationEntity, ...]
    schemas: tuple[OpenAPISchemaEntity, ...]
    security_schemes: tuple[OpenAPISecuritySchemeEntity, ...]
    parse_errors: tuple[OpenAPIParseError, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SCHEMA_REF_RE = re.compile(r"^#/components/schemas/(?P<name>[A-Za-z0-9_./-]+)$")
"""``$ref`` keys we resolve to a schema name; non-matching $refs are
left to the graph_builder for inter-doc linkage."""

_API_VERSION_FROM_PATH_RE = re.compile(r"^/api/(?P<v>\d+\.\d+(?:\.\d+)?)/")
"""Databricks API paths embed the version as ``/api/<v>/…``. We use this
to **verify** the caller-supplied ``api_version`` is consistent with
each operation's path; mismatches emit a parse error rather than going
silent."""


def _content_hash(*parts: str | None) -> str:
    """Stable sha256[:16] of joined parts (mirrors sdk_adapter's helper)."""

    h = hashlib.sha256()
    for p in parts:
        if p is None:
            h.update(b"\x00")
        else:
            h.update(p.encode("utf-8"))
            h.update(b"\x00")
    return h.hexdigest()[:16]


def _normalize_effect_class(raw: str | None) -> str | None:
    """Map an OpenAPI ``x-databricks-effect-class`` value to the closed
    capability-graph vocabulary.

    Per §23.4.2, ``extensions.effect_class ∈
    {read, write, write·hitl, unclassified}``. The OpenAPI extension's
    canonical value set is a superset (e.g., it may include
    ``read-only`` or ``mutating``); we normalize to the four enum values.
    Unrecognized values pass through as-is so the graph_builder can
    surface them via ``CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN`` for
    human review.
    """

    if raw is None:
        return None
    v = raw.strip().lower().replace("-", "").replace("_", "")
    if v in ("read", "readonly", "ro"):
        return "read"
    if v in ("write", "mutating", "mutate", "rw", "readwrite"):
        return "write"
    if v in ("writehitl", "writehumanapproval", "hitl", "destructive"):
        return "write·hitl"
    if v in ("unclassified", "unknown", "tbd"):
        return "unclassified"
    return raw  # passthrough; graph_builder handles the unknown


def _path_slug(path: str, http_method: str) -> str:
    """Synthesize a stable slug for paths whose operation lacks an
    operationId (a known spec-bug failure mode).

    Example: ``GET /api/2.1/jobs/{job_id}/runs`` → ``get_jobs_job_id_runs``
    """

    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_").lower()
    return f"{http_method.lower()}_{cleaned}"


def _extract_schema_names(node: Any) -> set[str]:
    """Walk an OpenAPI sub-tree and yield every schema name referenced
    via ``$ref: '#/components/schemas/<name>'``.

    Recurses into dicts and lists; non-container leaves are ignored.
    Returns a ``set`` so duplicate refs collapse.
    """

    out: set[str] = set()

    def _walk(n: Any) -> None:
        if isinstance(n, dict):
            ref = n.get("$ref")
            if isinstance(ref, str):
                m = _SCHEMA_REF_RE.match(ref)
                if m is not None:
                    out.add(m.group("name"))
            for v in n.values():
                _walk(v)
        elif isinstance(n, list):
            for v in n:
                _walk(v)

    _walk(node)
    return out


# ---------------------------------------------------------------------------
# Per-document walker
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _DocumentParse:
    """Internal: result of parsing one OpenAPI document."""

    document: OpenAPIDocumentEntity
    operations: tuple[OpenAPIOperationEntity, ...]
    schemas: tuple[OpenAPISchemaEntity, ...]
    security_schemes: tuple[OpenAPISecuritySchemeEntity, ...]


def _parse_one_document(
    *,
    inp: OpenAPIDocumentInput,
) -> _DocumentParse | OpenAPIParseError:
    """Parse a single OpenAPI 3.x document into typed entities.

    Returns either a :class:`_DocumentParse` aggregate or a typed
    :class:`OpenAPIParseError` on any structural issue. We never raise.
    """

    doc = inp.document
    api_version = inp.api_version
    source_url = inp.source_url

    if not isinstance(doc, Mapping):
        return OpenAPIParseError(
            api_version=api_version,
            source_url=source_url,
            error_kind="TypeError",
            error_message=f"document must be a Mapping, got {type(doc).__name__}",
        )

    # ---- info block ----------------------------------------------------
    info = doc.get("info") or {}
    if not isinstance(info, Mapping):
        return OpenAPIParseError(
            api_version=api_version,
            source_url=source_url,
            error_kind="TypeError",
            error_message=f"'info' must be a Mapping, got {type(info).__name__}",
        )
    info_title = info.get("title") if isinstance(info.get("title"), str) else None
    info_version = (
        info.get("version") if isinstance(info.get("version"), str) else None
    )
    info_description = (
        info.get("description")
        if isinstance(info.get("description"), str)
        else None
    )

    # ---- servers -------------------------------------------------------
    server_urls: list[str] = []
    servers = doc.get("servers") or []
    if isinstance(servers, list):
        for s in servers:
            if isinstance(s, Mapping):
                u = s.get("url")
                if isinstance(u, str):
                    server_urls.append(u)

    # ---- components/securitySchemes -----------------------------------
    security_schemes: list[OpenAPISecuritySchemeEntity] = []
    components = doc.get("components") or {}
    sec_block = components.get("securitySchemes") if isinstance(components, Mapping) else None
    if isinstance(sec_block, Mapping):
        for scheme_name, scheme in sorted(sec_block.items()):
            if not isinstance(scheme, Mapping):
                continue
            scheme_type = scheme.get("type") if isinstance(scheme.get("type"), str) else None
            # OAuth2 + http subtype lives in different keys; surface both.
            scheme_subtype: str | None
            if scheme_type == "http":
                scheme_subtype = scheme.get("scheme") if isinstance(scheme.get("scheme"), str) else None
            elif scheme_type == "oauth2":
                flows = scheme.get("flows")
                if isinstance(flows, Mapping):
                    # First declared flow type wins for the subtype field.
                    flow_keys = sorted(k for k in flows if isinstance(k, str))
                    scheme_subtype = flow_keys[0] if flow_keys else None
                else:
                    scheme_subtype = None
            else:
                scheme_subtype = None
            description = (
                scheme.get("description")
                if isinstance(scheme.get("description"), str)
                else None
            )
            scheme_id = f"openapi:{api_version}:security:{scheme_name}"
            security_schemes.append(
                OpenAPISecuritySchemeEntity(
                    scheme_id=scheme_id,
                    api_version=api_version,
                    scheme_name=scheme_name,
                    scheme_type=scheme_type,
                    scheme_subtype=scheme_subtype,
                    description=description,
                    source_url=source_url,
                    content_hash=_content_hash(
                        scheme_id, scheme_type, scheme_subtype, description
                    ),
                )
            )

    # ---- components/schemas -------------------------------------------
    schemas: list[OpenAPISchemaEntity] = []
    schemas_block = components.get("schemas") if isinstance(components, Mapping) else None
    if isinstance(schemas_block, Mapping):
        for schema_name, schema in sorted(schemas_block.items()):
            if not isinstance(schema, Mapping):
                continue
            schema_type = (
                schema.get("type") if isinstance(schema.get("type"), str) else None
            )
            description = (
                schema.get("description")
                if isinstance(schema.get("description"), str)
                else None
            )
            properties = schema.get("properties")
            if isinstance(properties, Mapping):
                property_names = tuple(sorted(str(k) for k in properties.keys()))
            else:
                property_names = ()
            required = schema.get("required")
            if isinstance(required, list):
                required_property_names = tuple(
                    sorted(str(r) for r in required if isinstance(r, str))
                )
            else:
                required_property_names = ()
            schema_id = f"openapi:{api_version}:schema:{schema_name}"
            schemas.append(
                OpenAPISchemaEntity(
                    schema_id=schema_id,
                    api_version=api_version,
                    schema_name=schema_name,
                    schema_type=schema_type,
                    description=description,
                    property_names=property_names,
                    required_property_names=required_property_names,
                    source_url=source_url,
                    content_hash=_content_hash(
                        schema_id,
                        schema_type,
                        description,
                        ",".join(property_names),
                        ",".join(required_property_names),
                    ),
                )
            )

    # ---- paths/operations ---------------------------------------------
    operations: list[OpenAPIOperationEntity] = []
    paths_block = doc.get("paths") or {}
    if not isinstance(paths_block, Mapping):
        return OpenAPIParseError(
            api_version=api_version,
            source_url=source_url,
            error_kind="TypeError",
            error_message=f"'paths' must be a Mapping, got {type(paths_block).__name__}",
        )

    for path, path_item in sorted(paths_block.items()):
        if not isinstance(path_item, Mapping):
            continue
        # Per-path version sanity check: paths embed the API version.
        m = _API_VERSION_FROM_PATH_RE.match(path)
        if m is not None and m.group("v") != api_version:
            # The caller's api_version disagrees with the path's
            # embedded version. We surface this as a parse error rather
            # than silently mis-tagging operations — mismatches
            # almost always indicate the caller bound the wrong source
            # URL to this api_version.
            return OpenAPIParseError(
                api_version=api_version,
                source_url=source_url,
                error_kind="ValueError",
                error_message=(
                    f"path={path!r} embeds api_version={m.group('v')!r}"
                    f" but caller bound api_version={api_version!r}"
                ),
            )

        for verb in sorted(_HTTP_METHODS):
            op = path_item.get(verb)
            if not isinstance(op, Mapping):
                continue
            op_id_raw = op.get("operationId")
            op_id_raw = op_id_raw if isinstance(op_id_raw, str) and op_id_raw else None
            slug = op_id_raw if op_id_raw is not None else f"{verb}:{_path_slug(path, verb)}"
            operation_id = f"openapi:{api_version}:{slug}"

            summary = op.get("summary") if isinstance(op.get("summary"), str) else None
            description = (
                op.get("description") if isinstance(op.get("description"), str) else None
            )
            effect_class_raw = (
                op.get("x-databricks-effect-class")
                if isinstance(op.get("x-databricks-effect-class"), str)
                else None
            )
            effect_class = _normalize_effect_class(effect_class_raw)

            # Schema refs from requestBody and responses.
            request_refs = _extract_schema_names(op.get("requestBody"))
            response_refs = _extract_schema_names(op.get("responses"))

            # Security refs: each operation.security item is a
            # ``{scheme_name: [scope, ...]}`` map; we collect just the keys.
            security_refs: set[str] = set()
            sec_list = op.get("security")
            if isinstance(sec_list, list):
                for sec_item in sec_list:
                    if isinstance(sec_item, Mapping):
                        for k in sec_item.keys():
                            if isinstance(k, str):
                                security_refs.add(k)

            deprecated = bool(op.get("deprecated"))
            tags_raw = op.get("tags")
            tags = (
                tuple(t for t in tags_raw if isinstance(t, str))
                if isinstance(tags_raw, list)
                else ()
            )

            operations.append(
                OpenAPIOperationEntity(
                    operation_id=operation_id,
                    api_version=api_version,
                    operation_id_raw=op_id_raw,
                    path=path,
                    http_method=verb.upper(),
                    summary=summary,
                    description=description,
                    effect_class_raw=effect_class_raw,
                    effect_class=effect_class,
                    request_schema_refs=tuple(sorted(request_refs)),
                    response_schema_refs=tuple(sorted(response_refs)),
                    security_scheme_refs=tuple(sorted(security_refs)),
                    deprecated=deprecated,
                    tags=tags,
                    source_url=source_url,
                    content_hash=_content_hash(
                        operation_id,
                        path,
                        verb,
                        op_id_raw,
                        effect_class_raw,
                        ",".join(sorted(request_refs)),
                        ",".join(sorted(response_refs)),
                    ),
                )
            )

    # ---- document entity -----------------------------------------------
    operation_ids = tuple(o.operation_id for o in operations)
    schema_ids = tuple(s.schema_id for s in schemas)
    security_scheme_ids = tuple(s.scheme_id for s in security_schemes)

    document_id = f"openapi:{api_version}:doc"
    document_entity = OpenAPIDocumentEntity(
        document_id=document_id,
        api_version=api_version,
        info_title=info_title,
        info_version=info_version,
        info_description=info_description,
        server_urls=tuple(server_urls),
        operation_ids=operation_ids,
        schema_ids=schema_ids,
        security_scheme_ids=security_scheme_ids,
        source_url=source_url,
        content_hash=_content_hash(
            document_id,
            info_title,
            info_version,
            *(o.content_hash for o in operations),
            *(s.content_hash for s in schemas),
        ),
    )

    return _DocumentParse(
        document=document_entity,
        operations=tuple(operations),
        schemas=tuple(schemas),
        security_schemes=tuple(security_schemes),
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def parse_openapi(
    *,
    documents: Sequence[OpenAPIDocumentInput],
    parsed_at_ms: int,
) -> OpenAPIAdapterResult:
    """Walk a batch of OpenAPI 3.x documents (one per API version).

    The caller (the indexer's ``extract_openapi`` task) passes pre-fetched
    documents; this function does no IO. Documents that fail to parse
    are isolated to :attr:`OpenAPIAdapterResult.parse_errors`; sibling
    documents continue parsing.

    The output entities are sorted by ``api_version`` then ``operation_id``
    (or the equivalent canonical id) for deterministic downstream
    persistence.
    """

    document_entities: list[OpenAPIDocumentEntity] = []
    operations: list[OpenAPIOperationEntity] = []
    schemas: list[OpenAPISchemaEntity] = []
    security_schemes: list[OpenAPISecuritySchemeEntity] = []
    parse_errors: list[OpenAPIParseError] = []

    for inp in documents:
        result = _parse_one_document(inp=inp)
        if isinstance(result, OpenAPIParseError):
            parse_errors.append(result)
            continue
        document_entities.append(result.document)
        operations.extend(result.operations)
        schemas.extend(result.schemas)
        security_schemes.extend(result.security_schemes)

    return OpenAPIAdapterResult(
        parsed_at_ms=parsed_at_ms,
        documents=tuple(sorted(document_entities, key=lambda d: d.api_version)),
        operations=tuple(sorted(operations, key=lambda o: o.operation_id)),
        schemas=tuple(sorted(schemas, key=lambda s: s.schema_id)),
        security_schemes=tuple(
            sorted(security_schemes, key=lambda s: s.scheme_id)
        ),
        parse_errors=tuple(parse_errors),
    )


__all__ = [
    "OpenAPIAdapterResult",
    "OpenAPIDocumentEntity",
    "OpenAPIDocumentInput",
    "OpenAPIOperationEntity",
    "OpenAPIParseError",
    "OpenAPISchemaEntity",
    "OpenAPISecuritySchemeEntity",
    "parse_openapi",
]
