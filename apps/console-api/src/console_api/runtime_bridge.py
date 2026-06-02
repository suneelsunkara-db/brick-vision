"""Adapter between the FastAPI sidecar and the Lakebase Postgres read substrate.

The FastAPI sidecar serves 7 read-only ``/api/knowledge/*`` endpoints
that back the Knowledge UI. The substrate they read from is the
collection of Synced Tables produced by the indexer Job's T14 publish
task in Lakebase Autoscaling Postgres (see
``docs/23-databricks-capability-graph.md`` §23.7 + ``publish.py``).

Connection model
================

* We open a fresh psycopg connection per request. Traffic is low
  (Knowledge UI, a handful of viewers) and a per-request connection
  keeps the OAuth-token-refresh story trivial: every connection
  carries the most recent token.
* The OAuth token is fetched via ``WorkspaceClient.database
  .generate_database_credential`` (Lakebase Autoscaling Postgres
  credential) and module-cached for 50 minutes — well under the 60-
  minute Postgres token TTL.
* The Postgres endpoint hostname is auto-resolved via
  ``WorkspaceClient.database.get_database_branch`` once at first
  connection and cached for the process lifetime (the host is stable
  per-branch). No ``BV_LAKEBASE_HOST`` env var is required — the
  operator only configures ``BV_LAKEBASE_PROJECT_ID`` /
  ``BV_LAKEBASE_BRANCH`` / ``BV_LAKEBASE_DATABASE``.
* The cached token + host are shared across worker threads (Uvicorn's
  default threadpool); a small ``threading.Lock`` guards refresh.

Fallbacks
=========

The sidecar must not 500 when Lakebase has not yet been provisioned
(local dev before T14 has ever run, or fresh installs that haven't
enabled Lakebase). When ``BV_LAKEBASE_PROJECT_ID`` /
``BV_LAKEBASE_DATABASE`` are unset, every list function returns an
empty list and every dict function returns the well-known "indexer
not yet run" banner shape that the SPA already renders gracefully.

Bridge boundary
===============

This module is now a compatibility façade for the older router contract.
Lakebase, Databricks SQL, Capability Graph, Workspace Context, RAG, and usecase
planning logic live in focused service modules.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import lakebase
from .usecase_service import (
    INTERNAL_UC_TABLE_PREFIXES as _INTERNAL_UC_TABLE_PREFIXES,
    PROFILE_PREDICATES as _PROFILE_PREDICATES,
    REQUIRED_PROFILE_PREDICATES as _REQUIRED_PROFILE_PREDICATES,
    REQUIRED_SUGGESTION_ANCHORS as _REQUIRED_SUGGESTION_ANCHORS,
)

# ---------------------------------------------------------------------------
# Banners + static lookups
# ---------------------------------------------------------------------------


_INDEXER_NOT_RUN_BANNER: dict[str, Any] = {
    "indexer_state": "never_run",
    "message": (
        "The Databricks Capability Graph indexer has not yet produced an "
        "active snapshot on this install. Until then, the capability "
        "graph is empty. See docs/23-databricks-capability-graph.md and "
        "run `brickvision indexer refresh` to bootstrap."
    ),
}


# ---------------------------------------------------------------------------
# Config + Lakebase compatibility helpers
# ---------------------------------------------------------------------------


_token_cache = lakebase.token_cache
_host_cache = lakebase.host_cache
_endpoint_name_cache = lakebase.endpoint_name_cache
_persistent_conn = lakebase.persistent_conn


def _bv_schema() -> str:
    """The UC schema where Synced Tables live (Postgres mirrors this name)."""

    return lakebase.bv_schema()


def _lakebase_configured() -> bool:
    """Whether the env has the minimum knobs for a Lakebase read attempt."""

    return lakebase.lakebase_configured()


def _lakebase_config_status() -> dict[str, Any]:
    """Return UI-safe Lakebase configuration diagnostics."""

    return lakebase.lakebase_config_status()


def _sanitize_ident(name: str) -> str:
    """Reject anything that isn't a safe Postgres identifier."""

    return lakebase.sanitize_ident(name)


def runtime_available() -> bool:
    """Whether the ``brickvision_runtime.capability_graph`` package is on path.

    Retained for backwards compatibility with health checks; the
    Knowledge endpoints no longer depend on the runtime package
    directly (they read from Lakebase via psycopg instead).
    """

    from .runtime_status import runtime_available as _impl

    return _impl()


def _resolve_lakebase_host() -> str:
    return lakebase.resolve_lakebase_host()


def _extract_pg_host_fallback(endpoints: Any, branch_name: str) -> str:  # noqa: ANN401
    return lakebase.extract_pg_host_fallback(endpoints, branch_name)


def _extract_pg_host(branch: Any) -> str:  # noqa: ANN401
    return lakebase.extract_pg_host(branch)


def _strip_to_host(url: str) -> str:
    return lakebase.strip_to_host(url)


def _resolve_lakebase_endpoint_name() -> str:
    return lakebase.resolve_lakebase_endpoint_name()


def _lakebase_oauth_credential() -> tuple[str, str]:
    return lakebase.lakebase_oauth_credential()


def _lakebase_connection() -> Any:
    return lakebase.lakebase_connection()


def _query_all(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    return lakebase.query_all(sql, params)


def _query_one(sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    return lakebase.query_one(sql, params)


def _active_snapshot() -> tuple[str, int] | None:
    """Return ``(snapshot_id, promoted_at_ms)`` from ``active_snapshot_id_synced``."""

    schema = _sanitize_ident(_bv_schema())
    row = _query_one(
        f"""
        SELECT snapshot_id, promoted_at_ms
        FROM {schema}.active_snapshot_id_synced
        WHERE singleton_key = %s
        """,
        ("singleton",),
    )
    if not row:
        return None
    return str(row[0]), int(row[1])


# ---------------------------------------------------------------------------
# /api/knowledge/corpus — list 5 corpus sources with per-source health
# ---------------------------------------------------------------------------


def get_capability_graph_corpus(*, user_id: str) -> dict[str, Any]:
    """List corpus sources and per-source state."""

    from .capability_graph_service import get_capability_graph_corpus as _impl

    return _impl(user_id=user_id)


def list_top_orders(*, user_id: str) -> list[dict[str, Any]]:
    """List the Top-Orders pinned to the active snapshot."""

    from .capability_graph_service import list_top_orders as _impl

    return _impl(user_id=user_id)


def list_meta_skills(
    *, user_id: str, top_order: str | None = None,
) -> list[dict[str, Any]]:
    """List Meta-Skills for the active snapshot."""

    from .capability_graph_service import list_meta_skills as _impl

    return _impl(user_id=user_id, top_order=top_order)


def list_extensions(
    *,
    user_id: str,
    meta_skill: str | None = None,
    has_exemplar: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List Extensions for the active snapshot."""

    from .capability_graph_service import list_extensions as _impl

    return _impl(
        user_id=user_id,
        meta_skill=meta_skill,
        has_exemplar=has_exemplar,
        limit=limit,
        offset=offset,
    )


def get_extension_provenance(
    *, user_id: str, extension_id: str,
) -> dict[str, Any]:
    """Provenance drill-down for an Extension."""

    from .capability_graph_service import get_extension_provenance as _impl

    return _impl(user_id=user_id, extension_id=extension_id)


def get_capability_graph_refresh_history(
    *, user_id: str, limit: int = 30,
) -> list[dict[str, Any]]:
    """Last N capability-graph indexer refreshes."""

    from .capability_graph_service import get_capability_graph_refresh_history as _impl

    return _impl(user_id=user_id, limit=limit)


def get_capability_graph_health(*, user_id: str) -> dict[str, Any]:
    """Capability-graph health rollup for the SPA status banner."""

    from .capability_graph_service import get_capability_graph_health as _impl

    return _impl(user_id=user_id)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


def search_capability_graph(
    *, user_id: str, query: str, limit: int = 10,
) -> dict[str, Any]:
    """Semantic search over the Capability Graph Vector Search index."""

    from .capability_rag_service import search_capability_graph as _impl

    if hasattr(search_capability_graph, "_vsc"):
        _impl._vsc = search_capability_graph._vsc  # type: ignore[attr-defined]
    result = _impl(user_id=user_id, query=query, limit=limit)
    if hasattr(_impl, "_vsc"):
        search_capability_graph._vsc = _impl._vsc  # type: ignore[attr-defined]
    return result


def _split_answer_and_code(full_response: str) -> tuple[str, str]:
    from .capability_rag_service import _split_answer_and_code as _impl

    return _impl(full_response)


def ask_capability_graph(
    *, user_id: str, question: str, top_k: int = 8,
) -> dict[str, Any]:
    """Retrieve, graph-expand, and generate grounded Capability Graph answers."""

    from .capability_rag_service import ask_capability_graph as _impl

    return _impl(user_id=user_id, question=question, top_k=top_k)


def get_workspace_kg_summary(*, user_id: str) -> dict[str, Any]:
    """Summarize the current partner Workspace KG read model."""

    from .workspace_context_service import get_workspace_kg_summary as _impl

    return _impl(user_id=user_id)


def list_workspace_kg_claims(
    *,
    user_id: str,
    q: str = "",
    subject_kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List/search current Workspace KG claims from Lakebase."""

    from .workspace_context_service import list_workspace_kg_claims as _impl

    return _impl(
        user_id=user_id,
        q=q,
        subject_kind=subject_kind,
        limit=limit,
        offset=offset,
    )


def list_workspace_build_suggestions(*, user_id: str, limit: int = 12) -> dict[str, Any]:
    """Compile evidence-backed usecase suggestions from live graph + Workspace KG."""

    from .usecase_service import list_workspace_build_suggestions as _impl

    return _impl(user_id=user_id, limit=limit)


def plan_and_build_workspace_suggestion(
    *, user_id: str, suggestion_id: str,
) -> dict[str, Any]:
    """Persist, evidence-check, and execute one safe usecase artifact."""

    from .usecase_service import plan_and_build_workspace_suggestion as _impl

    return _impl(user_id=user_id, suggestion_id=suggestion_id)


def _execute_sql_statement(statement: str) -> None:
    from .databricks_sql import execute_sql_statement

    execute_sql_statement(statement)


def _query_sql_statement_rows(statement: str) -> list[list[Any]]:
    from .databricks_sql import query_sql_statement_rows

    return query_sql_statement_rows(statement)


def _resolve_warehouse_id() -> str:
    from .databricks_sql import resolve_warehouse_id

    return resolve_warehouse_id()


def _qualified_uc_name(object_name: str) -> str:
    from .databricks_sql import qualified_uc_name

    return qualified_uc_name(object_name)


def _quote_identifier(value: str) -> str:
    from .databricks_sql import quote_identifier

    return quote_identifier(value)


def _sql_string_literal(value: str) -> str:
    from .databricks_sql import sql_string_literal

    return sql_string_literal(value)


def _workspace_claim_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    from .workspace_context_service import workspace_claim_row_to_dict

    return workspace_claim_row_to_dict(row)


def _decode_json(value: Any) -> Any:  # noqa: ANN401
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value


__all__ = [
    "ask_capability_graph",
    "get_capability_graph_corpus",
    "get_capability_graph_health",
    "get_capability_graph_refresh_history",
    "get_workspace_kg_summary",
    "get_extension_provenance",
    "list_workspace_build_suggestions",
    "list_workspace_kg_claims",
    "list_extensions",
    "list_meta_skills",
    "list_top_orders",
    "plan_and_build_workspace_suggestion",
    "runtime_available",
    "search_capability_graph",
]
