"""Workspace Context read model service."""

from __future__ import annotations

import json
from typing import Any

from .workspace_visibility import workspace_claim_visibility_where


def get_workspace_kg_summary(*, user_id: str) -> dict[str, Any]:
    """Summarize the current partner Workspace KG read model."""

    b = _bridge()
    if not b._lakebase_configured():
        status = b._lakebase_config_status()
        return {
            "claim_count": 0,
            "subject_count": 0,
            "last_observed_at_ms": None,
            "last_run_id": None,
            "by_kind": [],
            "indexer_state": "lakebase_not_configured",
            "message": status["message"],
            "lakebase_config": status,
        }

    schema = b._sanitize_ident(b._bv_schema())
    visibility_where = workspace_claim_visibility_where()
    total = b._query_one(
        f"""
        SELECT
          count(*) AS claim_count,
          count(DISTINCT subject) AS subject_count,
          max(observed_at_ms) AS last_observed_at_ms,
          max(run_id) AS last_run_id
        FROM {schema}.workspace_claims_current_synced
        WHERE {visibility_where}
        """,
    )
    by_kind_rows = b._query_all(
        f"""
        SELECT subject_kind, count(*) AS claim_count
        FROM {schema}.workspace_claims_current_synced
        WHERE {visibility_where}
        GROUP BY subject_kind
        ORDER BY claim_count DESC, subject_kind ASC
        """,
    )
    if not total:
        return {
            "claim_count": 0,
            "subject_count": 0,
            "last_observed_at_ms": None,
            "last_run_id": None,
            "by_kind": [],
            "indexer_state": "never_run",
            "message": (
                "The Workspace KG serverless refresh has not yet published "
                "workspace_claims_current_synced to Lakebase."
            ),
        }

    return {
        "claim_count": int(total[0] or 0),
        "subject_count": int(total[1] or 0),
        "last_observed_at_ms": int(total[2]) if total[2] is not None else None,
        "last_run_id": str(total[3]) if total[3] is not None else None,
        "by_kind": [
            {"subject_kind": str(row[0]), "claim_count": int(row[1] or 0)}
            for row in by_kind_rows
        ],
        "indexer_state": "active",
    }


def list_workspace_kg_claims(
    *,
    user_id: str,
    q: str = "",
    subject_kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List/search current Workspace KG claims from Lakebase."""

    b = _bridge()
    schema = b._sanitize_ident(b._bv_schema())
    bounded_limit = max(1, min(int(limit), 250))
    bounded_offset = max(0, int(offset))
    where: list[str] = [workspace_claim_visibility_where()]
    params: list[Any] = []

    if subject_kind:
        where.append("subject_kind = %s")
        params.append(subject_kind)
    if q.strip():
        like = f"%{q.strip()}%"
        where.append(
            "(subject ILIKE %s OR predicate ILIKE %s OR object_ref ILIKE %s "
            "OR metadata_json ILIKE %s)"
        )
        params.extend([like, like, like, like])

    where_sql = f"WHERE {' AND '.join(where)}"
    rows = b._query_all(
        f"""
        SELECT
          claim_id,
          workspace_profile_id,
          workspace_id,
          subject,
          subject_kind,
          predicate,
          object_ref,
          value_json,
          metadata_json,
          source_skill_id,
          confidence,
          observed_at_ms,
          run_id
        FROM {schema}.workspace_claims_current_synced
        {where_sql}
        ORDER BY subject_kind ASC, subject ASC
        LIMIT %s OFFSET %s
        """,
        (*params, bounded_limit, bounded_offset),
    )
    total = b._query_one(
        f"""
        SELECT count(*)
        FROM {schema}.workspace_claims_current_synced
        {where_sql}
        """,
        tuple(params),
    )

    return {
        "claims": [workspace_claim_row_to_dict(row) for row in rows],
        "total": int(total[0] or 0) if total else 0,
        "query": q,
        "subject_kind": subject_kind,
        "limit": bounded_limit,
        "offset": bounded_offset,
    }


def workspace_claim_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "claim_id": str(row[0]),
        "workspace_profile_id": str(row[1]),
        "workspace_id": str(row[2]) if row[2] is not None else None,
        "subject": str(row[3]),
        "subject_kind": str(row[4]),
        "predicate": str(row[5]),
        "object_ref": str(row[6]) if row[6] is not None else None,
        "value": _decode_json(row[7]),
        "metadata": _decode_json(row[8]),
        "source_skill_id": str(row[9]),
        "confidence": float(row[10]) if row[10] is not None else None,
        "observed_at_ms": int(row[11]) if row[11] is not None else None,
        "run_id": str(row[12]) if row[12] is not None else None,
    }


def _decode_json(value: Any) -> Any:  # noqa: ANN401
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _bridge() -> Any:
    from . import runtime_bridge

    return runtime_bridge

