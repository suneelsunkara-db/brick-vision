"""Capability Graph read model service."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SOURCE_URL_ROOTS: dict[str, str] = {
    "sdk": "https://github.com/databricks/databricks-sdk-py",
    "openapi": "https://docs.databricks.com/api/workspace/",
    "docs": "https://docs.databricks.com/",
    "blog": "https://www.databricks.com/blog/",
    "labs": "https://github.com/databrickslabs/lakebridge",
}


def get_capability_graph_corpus(*, user_id: str) -> dict[str, Any]:
    """List the 5 corpus sources + per-source state.

    Joins ``corpus_health_synced`` (one row per refresh × source — we
    pick the most recent per ``source_kind``) with ``source_authority
    _synced`` (closed-set authority weights). Returns ``url_root`` from
    the static lookup since ``corpus_health`` doesn't carry it.
    """

    snapshot = _bridge()._active_snapshot()
    if snapshot is None:
        return {"sources": [], **_bridge()._INDEXER_NOT_RUN_BANNER}

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    rows = _bridge()._query_all(
        f"""
        WITH latest AS (
          SELECT source_kind, MAX(recorded_at_ms) AS recorded_at_ms
          FROM {schema}.corpus_health_synced
          WHERE source_kind <> 'aggregate'
          GROUP BY source_kind
        ),
        latest_authority AS (
          SELECT source_kind, MAX(schema_version) AS schema_version
          FROM {schema}.source_authority_synced
          GROUP BY source_kind
        )
        SELECT
          h.source_kind,
          h.last_refresh_at_ms,
          h.last_refresh_status,
          h.entity_count,
          a.authority_weight
        FROM {schema}.corpus_health_synced AS h
        JOIN latest AS l
          ON l.source_kind = h.source_kind
         AND l.recorded_at_ms = h.recorded_at_ms
        LEFT JOIN latest_authority AS la ON la.source_kind = h.source_kind
        LEFT JOIN {schema}.source_authority_synced AS a
          ON a.source_kind = la.source_kind
         AND a.schema_version = la.schema_version
        ORDER BY h.source_kind
        """,
    )

    sources = [
        {
            "source_id": str(r[0]),
            "url_root": SOURCE_URL_ROOTS.get(str(r[0]), ""),
            "source_authority": float(r[4]) if r[4] is not None else None,
            "last_refresh_ts": int(r[1]) if r[1] is not None else None,
            "state": str(r[2]) if r[2] is not None else "unknown",
            "extension_count": int(r[3]) if r[3] is not None else 0,
        }
        for r in rows
    ]

    return {
        "sources": sources,
        "active_snapshot_id": snapshot[0],
        "promoted_at_ms": snapshot[1],
        "indexer_state": "active",
    }


# ---------------------------------------------------------------------------
# /api/knowledge/top-orders — 7 top-orders for the active snapshot
# ---------------------------------------------------------------------------


def list_top_orders(*, user_id: str) -> list[dict[str, Any]]:
    """List the 7 Top-Orders pinned to the currently-active snapshot."""

    snapshot = _bridge()._active_snapshot()
    if snapshot is None:
        return []

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    rows = _bridge()._query_all(
        f"""
        SELECT
          top_order_id,
          title,
          meta_skill_count,
          extension_count,
          hand_authored_exemplar_count
        FROM {schema}.top_orders_synced
        WHERE snapshot_id = %s
        ORDER BY top_order_id
        """,
        (snapshot[0],),
    )
    return [
        {
            "top_order_id": str(r[0]),
            "label": str(r[1]),
            "meta_skill_count": int(r[2]) if r[2] is not None else 0,
            "extension_count": int(r[3]) if r[3] is not None else 0,
            "hand_authored_exemplar_count": int(r[4]) if r[4] is not None else 0,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# /api/knowledge/meta-skills — Meta-Skills (optionally filtered by Top-Order)
# ---------------------------------------------------------------------------


def list_meta_skills(
    *, user_id: str, top_order: str | None = None,
) -> list[dict[str, Any]]:
    """List Meta-Skills for the active snapshot.

    Adds two derived counts the SPA renders as badges:

    * ``extension_count`` — number of Extensions whose ``meta_skill_id``
      points at this row, for the same snapshot.
    * ``hand_authored_exemplar_count`` — same join, but counting only
      Extensions where ``exemplar_skill_id IS NOT NULL`` (i.e. the
      indexer linked a hand-authored Layer-0 skill as the named
      exemplar — see §23.2.6).
    """

    snapshot = _bridge()._active_snapshot()
    if snapshot is None:
        return []

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    sql = f"""
        SELECT
          m.meta_skill_id,
          m.title,
          m.top_order_id,
          COUNT(e.extension_id) AS extension_count,
          SUM(CASE WHEN e.exemplar_skill_id IS NOT NULL THEN 1 ELSE 0 END)
            AS hand_authored_exemplar_count
        FROM {schema}.meta_skills_synced AS m
        LEFT JOIN {schema}.extensions_synced AS e
          ON e.snapshot_id = m.snapshot_id
         AND e.meta_skill_id = m.meta_skill_id
        WHERE m.snapshot_id = %s
        """
    params: list[Any] = [snapshot[0]]
    if top_order:
        sql += " AND m.top_order_id = %s"
        params.append(top_order)
    sql += """
        GROUP BY m.meta_skill_id, m.title, m.top_order_id
        ORDER BY m.meta_skill_id
        """
    rows = _bridge()._query_all(sql, tuple(params))
    return [
        {
            "meta_skill_id": str(r[0]),
            "label": str(r[1]),
            "parent_top_order": str(r[2]),
            "extension_count": int(r[3]) if r[3] is not None else 0,
            "hand_authored_exemplar_count": int(r[4]) if r[4] is not None else 0,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# /api/knowledge/extensions — Extensions (optionally filtered)
# ---------------------------------------------------------------------------


def list_extensions(
    *,
    user_id: str,
    meta_skill: str | None = None,
    has_exemplar: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List Extensions for the active snapshot.

    Filters:

    * ``meta_skill`` — exact match on ``meta_skill_id``.
    * ``has_exemplar`` — when True, only rows with a non-null
      ``exemplar_skill_id``; when False, only rows whose exemplar slot
      is empty.

    Pagination uses LIMIT / OFFSET — fine at this scale (~750 rows).
    """

    snapshot = _bridge()._active_snapshot()
    if snapshot is None:
        return []

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    safe_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))

    sql = f"""
        SELECT
          extension_id,
          title,
          meta_skill_id,
          effect_class,
          cloud_variance,
          exemplar_skill_id
        FROM {schema}.extensions_synced
        WHERE snapshot_id = %s
        """
    params: list[Any] = [snapshot[0]]
    if meta_skill:
        sql += " AND meta_skill_id = %s"
        params.append(meta_skill)
    if has_exemplar is True:
        sql += " AND exemplar_skill_id IS NOT NULL"
    elif has_exemplar is False:
        sql += " AND exemplar_skill_id IS NULL"
    sql += " ORDER BY extension_id LIMIT %s OFFSET %s"
    params.extend([safe_limit, safe_offset])

    rows = _bridge()._query_all(sql, tuple(params))
    return [
        {
            "extension_id": str(r[0]),
            "label": str(r[1]),
            "parent_meta_skill": str(r[2]),
            "effect_class": str(r[3]) if r[3] is not None else "unclassified",
            "cloud_variance": str(r[4]) if r[4] is not None else "invariant",
            "has_exemplar": r[5] is not None,
            "exemplar_skill_id": str(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# /api/knowledge/extensions/{id}/provenance — drill-down pane
# ---------------------------------------------------------------------------


def _synthesize_neighbors_from_vs(extension_id: str) -> list[dict[str, Any]]:
    """Synthesize 2-hop neighbor data from Vector Search when Lakebase is unavailable.

    Uses the extension_id as a search query to find related entities via
    semantic similarity. This provides a meaningful graph visualization
    even before entity_edges are computed by the kg_extractor pipeline.
    """

    try:
        query_text = (
            extension_id
            .replace("meta:", "")
            .replace("ext:", "")
            .replace("/", " ")
            .replace("-", " ")
        )
        results = _bridge().search_capability_graph(
            user_id="system",
            query=query_text,
            limit=12,
        )
        hits = results.get("results", [])

        neighbors: list[dict[str, Any]] = []
        seen: set[str] = set()

        for hit in hits:
            eid = hit.get("entity_id", "")
            if not eid or eid == extension_id or eid in seen:
                continue
            seen.add(eid)

            kind = hit.get("entity_kind", "")
            meta_skill = hit.get("meta_skill_id", "") or ""

            ext_meta = extension_id.split("/")[0] if "/" in extension_id else ""
            is_sibling = meta_skill and meta_skill == ext_meta
            relation = "sibling" if is_sibling else "mentions"
            hop = 1 if is_sibling or len(neighbors) < 5 else 2

            neighbors.append({
                "extension_id": eid,
                "label": hit.get("chunk_text", "")[:60] or None,
                "relation": relation,
                "hop": hop,
            })

        return neighbors
    except Exception:
        return []


def get_extension_provenance(
    *, user_id: str, extension_id: str,
) -> dict[str, Any]:
    """Provenance drill-down for an Extension.

    Stitches three Synced tables for the active snapshot:

    1. ``extensions_synced``       — top-level identity (label, parent
       Meta-Skill, effect class, cloud variance, authority).
    2. ``source_provenance_synced`` — the chunks that contributed
       evidence (source URL / file / commit_sha / parsed_at).
    3. ``entity_edges_synced``      — multi-hop neighbours via recursive
       CTE with PPR-style decay (up to 4 hops, cycle-safe path tracking,
       bounded at 200 nodes for UI performance).
    """

    payload: dict[str, Any] = {
        "extension_id": extension_id,
        "label": None,
        "parent_meta_skill": None,
        "effect_class": None,
        "cloud_variance": None,
        "authority_score": None,
        "authority_scorer": None,
        "cross_cloud_note": None,
        "contributing_chunks": [],
        "two_hop_neighbors": [],
    }
    snapshot = _bridge()._active_snapshot()
    if snapshot is None:
        neighbors = _synthesize_neighbors_from_vs(extension_id)
        return {
            **payload,
            **_bridge()._INDEXER_NOT_RUN_BANNER,
            "two_hop_neighbors": neighbors,
        }

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())

    ext_row = _bridge()._query_one(
        f"""
        SELECT title, meta_skill_id, effect_class, cloud_variance, authority
        FROM {schema}.extensions_synced
        WHERE snapshot_id = %s AND extension_id = %s
        """,
        (snapshot[0], extension_id),
    )
    if ext_row is None:
        return {
            **payload,
            "active_snapshot_id": snapshot[0],
            "promoted_at_ms": snapshot[1],
            "indexer_state": "active",
        }

    payload["label"] = str(ext_row[0]) if ext_row[0] else None
    payload["parent_meta_skill"] = str(ext_row[1]) if ext_row[1] else None
    payload["effect_class"] = str(ext_row[2]) if ext_row[2] else None
    payload["cloud_variance"] = str(ext_row[3]) if ext_row[3] else None
    payload["authority_scorer"] = (
        str(ext_row[4]) if ext_row[4] else None
    )

    # Chunks — one provenance row per (source_kind, ref). Join to
    # source_authority for the per-chunk authority badge.
    chunk_rows = _bridge()._query_all(
        f"""
        WITH latest_authority AS (
          SELECT source_kind, MAX(schema_version) AS schema_version
          FROM {schema}.source_authority_synced
          GROUP BY source_kind
        )
        SELECT
          p.source_kind,
          p.ref,
          p.commit_sha,
          p.parsed_at_ms,
          a.authority_weight
        FROM {schema}.source_provenance_synced AS p
        LEFT JOIN latest_authority AS la ON la.source_kind = p.source_kind
        LEFT JOIN {schema}.source_authority_synced AS a
          ON a.source_kind = la.source_kind
         AND a.schema_version = la.schema_version
        WHERE p.snapshot_id = %s AND p.entity_id = %s
        ORDER BY p.source_kind, p.ref
        """,
        (snapshot[0], extension_id),
    )
    payload["contributing_chunks"] = [
        _provenance_chunk(row)
        for row in chunk_rows
    ]

    # Neighbours — multi-hop using recursive CTE with PPR-style decay.
    # Max depth = 4 hops, bounded to 200 rows total for UI safety.
    neighbor_rows = _bridge()._query_all(
        f"""
        WITH RECURSIVE graph_walk(entity_id, depth, relation, path) AS (
            SELECT
                CASE WHEN e.src_id = %s THEN e.dst_id ELSE e.src_id END,
                1,
                e.edge_kind,
                ARRAY[%s, CASE WHEN e.src_id = %s THEN e.dst_id ELSE e.src_id END]
            FROM {schema}.entity_edges_synced AS e
            WHERE e.snapshot_id = %s
              AND (e.src_id = %s OR e.dst_id = %s)
            UNION ALL
            SELECT
                CASE WHEN e.src_id = w.entity_id THEN e.dst_id ELSE e.src_id END,
                w.depth + 1,
                e.edge_kind,
                w.path || CASE WHEN e.src_id = w.entity_id THEN e.dst_id ELSE e.src_id END
            FROM graph_walk w
            JOIN {schema}.entity_edges_synced e
              ON e.snapshot_id = %s
              AND (e.src_id = w.entity_id OR e.dst_id = w.entity_id)
            WHERE w.depth < 4
              AND NOT (
                CASE WHEN e.src_id = w.entity_id THEN e.dst_id ELSE e.src_id END
              ) = ANY(w.path)
        )
        SELECT DISTINCT ON (gw.entity_id)
            gw.entity_id,
            x.title AS label,
            gw.relation,
            gw.depth AS hop
        FROM graph_walk gw
        LEFT JOIN {schema}.extensions_synced x
          ON x.snapshot_id = %s AND x.extension_id = gw.entity_id
        WHERE gw.entity_id <> %s
        ORDER BY gw.entity_id, gw.depth
        LIMIT 200
        """,
        (
            extension_id, extension_id, extension_id,
            snapshot[0], extension_id, extension_id,
            snapshot[0],
            snapshot[0], extension_id,
        ),
    )
    payload["two_hop_neighbors"] = [
        {
            "extension_id": str(row[0]) if row[0] else "",
            "label": str(row[1]) if row[1] else None,
            "relation": str(row[2]) if row[2] else "related",
            "hop": int(row[3]),
        }
        for row in neighbor_rows
        if row[0]
    ]

    if not payload["two_hop_neighbors"]:
        payload["two_hop_neighbors"] = _synthesize_neighbors_from_vs(extension_id)

    return {
        **payload,
        "active_snapshot_id": snapshot[0],
        "promoted_at_ms": snapshot[1],
        "indexer_state": "active",
    }


def _provenance_chunk(row: tuple[Any, ...]) -> dict[str, Any]:
    """Map a source_provenance row to the SPA's ProvenanceChunk shape.

    We don't materialise file_path / line_start / line_end / signed_by /
    scorer separately at v0.7.7 ship — they live in the ``ref`` field
    which is source-kind-specific (URL for docs/blog/openapi, file:line
    for sdk, github URL for labs). The SPA renders ``ref`` as
    ``source_url`` so the chunk card stays useful even before we split
    these out.
    """

    source_kind, ref, commit_sha, parsed_at_ms, authority_weight = row
    return {
        "source_id": str(source_kind) if source_kind else "",
        "source_url": str(ref) if ref else None,
        "file_path": None,
        "line_start": None,
        "line_end": None,
        "commit_sha": str(commit_sha) if commit_sha else None,
        "parsed_at_ms": int(parsed_at_ms) if parsed_at_ms is not None else None,
        "signed_by": None,
        "authority_score": (
            float(authority_weight) if authority_weight is not None else None
        ),
        "scorer": None,
    }


# ---------------------------------------------------------------------------
# /api/knowledge/refresh-history — last N indexer runs
# ---------------------------------------------------------------------------


def get_capability_graph_refresh_history(
    *, user_id: str, limit: int = 30,
) -> list[dict[str, Any]]:
    """Last N capability-graph indexer refreshes.

    Reads ``refresh_plan_synced`` directly. The ledger row carries
    ``planned_at_ms`` (start), ``duration_ms`` (so we can derive an
    ``ended_at_ms``), ``result_status`` (the run state), ``result_
    snapshot_id`` (null until a successful promote), and ``partial_
    sources``. The SPA's ``rejection_reason_code`` and
    ``total_input_tokens`` slots are surfaced as ``None`` for now —
    populating them requires the indexer to write them, which is a
    separate piece of the pipeline.
    """

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    safe_limit = max(1, min(int(limit), 200))
    rows = _bridge()._query_all(
        f"""
        SELECT
          refresh_plan_id,
          planned_at_ms,
          duration_ms,
          result_snapshot_id,
          result_status,
          partial_sources
        FROM {schema}.refresh_plan_synced
        ORDER BY planned_at_ms DESC
        LIMIT %s
        """,
        (safe_limit,),
    )
    return [
        {
            "run_id": str(r[0]),
            "started_at_ms": int(r[1]) if r[1] is not None else 0,
            "ended_at_ms": (
                int(r[1]) + int(r[2])
                if r[1] is not None and r[2] is not None
                else None
            ),
            "snapshot_id": str(r[3]) if r[3] else "",
            "state": str(r[4]) if r[4] else "unknown",
            "rejection_reason_code": None,
            "partial_sources": list(r[5]) if r[5] else [],
            "total_input_tokens": 0,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# /api/knowledge/health — UI status banner rollup
# ---------------------------------------------------------------------------


def get_capability_graph_health(*, user_id: str) -> dict[str, Any]:
    """Capability-graph health rollup for the SPA's status banner.

    Returns the well-known "indexer never run" shape when the active-
    snapshot pointer hasn't been published yet. Otherwise computes
    freshness vs. ``BV_INDEXER_FRESHNESS_TOLERANCE_DAYS`` (default 2)
    and pulls partial-source flags + smoke pass-rate from the most
    recent ``refresh_plan_synced`` row that produced this snapshot.
    """

    tolerance_days = int(os.environ.get("BV_INDEXER_FRESHNESS_TOLERANCE_DAYS", "2"))
    base: dict[str, Any] = {
        "active_snapshot_id": None,
        "freshness_days": None,
        "freshness_tolerance_days": tolerance_days,
        "is_stale": False,
        "is_missing": True,
        "smoke_baseline_pass_rate": None,
        "smoke_locked_v1": None,
        "partial_sources": [],
    }

    snapshot = _bridge()._active_snapshot()
    if snapshot is None:
        return {**base, **_bridge()._INDEXER_NOT_RUN_BANNER}

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    plan_row = _bridge()._query_one(
        f"""
        SELECT partial_sources, planned_at_ms, duration_ms
        FROM {schema}.refresh_plan_synced
        WHERE result_snapshot_id = %s
        ORDER BY planned_at_ms DESC
        LIMIT 1
        """,
        (snapshot[0],),
    )

    promoted_at_ms = snapshot[1]
    now_ms = int(_bridge().time.time() * 1000)
    freshness_days = max(0, (now_ms - promoted_at_ms) // (24 * 3600 * 1000))
    is_stale = freshness_days > tolerance_days

    return {
        "active_snapshot_id": snapshot[0],
        "freshness_days": int(freshness_days),
        "freshness_tolerance_days": tolerance_days,
        "is_stale": is_stale,
        "is_missing": False,
        "smoke_baseline_pass_rate": None,
        "smoke_locked_v1": None,
        "partial_sources": list(plan_row[0]) if plan_row and plan_row[0] else [],
        "indexer_state": "active",
        "promoted_at_ms": promoted_at_ms,
    }




def _bridge() -> Any:
    from . import runtime_bridge

    return runtime_bridge
