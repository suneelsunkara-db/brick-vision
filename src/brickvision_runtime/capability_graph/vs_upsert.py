"""Indexer task 10 — upsert embeddings into Mosaic AI Vector Search
(per §23.3.8).

Materializes the :class:`EmbeddingCacheRow` rows produced by
:mod:`embed` into the v0.7.7 Vector Search index at
``<BV_CATALOG>.<BV_SCHEMA>.entity_index``. Vector Search is the
**fast-retrieval substrate** for the dual-substrate kg_search
(§23.2.8): the Delta tables hold authoritative source-of-truth, the
vector index holds an embedded mirror so retrieval can use ANN search.

Why we use direct upsert instead of delta-sync mode
====================================================

Mosaic AI Vector Search supports two index modes:
  1. **Delta-sync** — the index auto-tracks a Delta table; embeddings
     are computed on the VS side via a managed embedding endpoint.
  2. **Direct upsert** — the indexer pushes embeddings directly
     (BYO-embeddings), VS just stores and retrieves.

We use direct upsert for v0.7.7 because:
  * The chunking grammar is shared between docs_adapter and
    blog_adapter (1500/150-token); doing embeddings ourselves keeps
    the chunking-vs-embedding contract in one place.
  * Direct upsert lets the embedding cache (:class:`EmbeddingCacheRow`)
    do its work — VS-managed embeddings would re-embed every chunk
    on every sync, defeating the cache.
  * Direct upsert is the only mode that supports our ~$0.60/day
    incremental refresh cost target; delta-sync would charge for
    re-embedding every chunk in every snapshot.

§23.3.8 reservation: in v0.7.8+ we may add a delta-sync mirror
specifically for the docs corpus (where embedding-on-read query
expansion would help cross-cloud retrieval), but for v0.7.7 ship,
single-mode direct upsert is the only path.

Discipline rule 15 (N189) — production-only upsert
==================================================

This module previously declared a ``VectorSearchClient(Protocol)``
and accepted a ``client`` parameter so offline tests could capture
upserts in memory. Per [`docs/01-overview.md`](
../../../../docs/01-overview.md) §0 +
[`docs/10-generation-philosophy.md`](
../../../../docs/10-generation-philosophy.md) §8.6 that Protocol seam
was retired. The production code path now calls
:class:`databricks.vector_search.client.VectorSearchClient` directly
(lazy-imported); the ``BV_DRY_RUN=true`` env-gate (per
[`docs/19-local-development.md`](../../../../docs/19-local-development.md)
§15.2.1) short-circuits the SDK call and instead writes the per-batch
upsert payload shape to
``tests/fixtures/capability_graph/last_vs_upsert_payload.json``
(override via ``BV_DRY_RUN_VS_UPSERT_LOG``).

Reason codes
============

Per §23.3.8:
  * :data:`ReasonCode.CAPABILITY_GRAPH_VS_UPSERT_FAILED` — emitted
    on per-batch failure; sibling batches continue. The indexer's
    ``promote`` task uses :attr:`VsUpsertResult.errors` as a hard
    gate (a partial vector index is worse than a stale one because
    retrieval results would silently drift).
  * :data:`ReasonCode.CAPABILITY_GRAPH_VS_INDEX_ENDPOINT_DOWN` — emitted
    by a pre-flight check ahead of upsert; not surfaced from this
    module (the check lives in the indexer Job's task wrapper).
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .schemas.types import EmbeddingCacheRow


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class VsUpsertError:
    """Per-batch failure (sibling batches continue)."""

    batch_index: int
    primary_keys: tuple[str, ...]
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class VsUpsertResult:
    """Aggregate output of one ``vs_upsert_embeddings`` invocation."""

    rows_upserted: int
    batches_attempted: int
    batches_succeeded: int
    retries: int
    errors: tuple[VsUpsertError, ...]
    started_at_ms: int
    completed_at_ms: int
    duration_ms: int
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Defaults (per §23.3.8)
# ---------------------------------------------------------------------------


_DEFAULT_BATCH_SIZE: int = 1000
"""Mosaic AI Vector Search direct-upsert sweet spot. Larger batches
hit per-request payload limits; smaller batches under-utilize the
TCP connection overhead."""

_MAX_RETRIES: int = 3
_RETRY_BASE_DELAY_S: float = 0.5  # 0.5, 1.0, 2.0


_DEFAULT_DRY_RUN_LOG = "tests/fixtures/capability_graph/last_vs_upsert_payload.json"


# ---------------------------------------------------------------------------
# Production upsert — VectorSearchClient wrapper with BV_DRY_RUN log
# ---------------------------------------------------------------------------


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "false").lower() in ("1", "true", "yes")


def _resolve_dry_run_log_path() -> Path:
    raw = os.environ.get("BV_DRY_RUN_VS_UPSERT_LOG", "").strip()
    return Path(raw) if raw else Path(_DEFAULT_DRY_RUN_LOG)


def _upsert_via_vs_sdk(
    *,
    index_name: str,
    primary_keys: Sequence[str],
    embeddings: Sequence[Sequence[float]],
    metadata: Sequence[Mapping[str, Any]],
) -> int:
    """Production upsert call against Mosaic AI Vector Search.

    Lazy-imports :mod:`databricks.vector_search`; offline tests with
    ``BV_DRY_RUN=true`` never reach this function.
    """

    from databricks.vector_search.client import VectorSearchClient  # noqa: PLC0415

    if not hasattr(_upsert_via_vs_sdk, "_client"):
        _upsert_via_vs_sdk._client = VectorSearchClient()  # type: ignore[attr-defined]
    index = _upsert_via_vs_sdk._client.get_index(index_name=index_name)  # type: ignore[attr-defined]
    rows: list[dict[str, Any]] = []
    for pk, vec, meta in zip(primary_keys, embeddings, metadata):
        row: dict[str, Any] = {"id": pk, "embedding": list(vec)}
        row.update(dict(meta))
        rows.append(row)
    result = index.upsert(rows)
    if isinstance(result, dict) and result.get("status") == "FAILURE":
        failed = result.get("result", {}).get("failed_primary_keys", [])
        raise RuntimeError(
            f"VS upsert FAILURE: {len(failed)}/{len(rows)} rows failed. "
            f"Sample keys: {failed[:5]}"
        )
    return len(rows)


def _log_dry_run_batches(
    *,
    index_name: str,
    batches: list[dict[str, Any]],
) -> None:
    target = _resolve_dry_run_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"index_name": index_name, "batches": batches},
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def vs_upsert_embeddings(
    *,
    embeddings: Sequence[EmbeddingCacheRow],
    entity_metadata: Mapping[str, Mapping[str, Any]],
    index_name: str,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    started_at_ms: int,
    completed_at_ms: int | None = None,
    sleep: Callable[[float], None] = lambda _s: None,
) -> VsUpsertResult:
    """Upsert N embeddings into Mosaic AI Vector Search.

    Per N189 / discipline rule 15 the writer is no longer a Protocol
    seam — :func:`_upsert_via_vs_sdk` ships the production
    Vector Search SDK call directly, with ``BV_DRY_RUN=true``
    short-circuiting to a fixture log. Tests exercising upsert set
    the env-gate, run the function, and inspect
    ``tests/fixtures/capability_graph/last_vs_upsert_payload.json``.

    Parameters
    ----------
    embeddings : Sequence[EmbeddingCacheRow]
        From :func:`embed.embed_batch`'s ``rows`` field.
    entity_metadata : Mapping[str, Mapping[str, Any]]
        ``content_hash -> {entity_id, entity_kind, snapshot_id, ...}``
        — the per-entity metadata that VS stores alongside each
        vector for filtered retrieval. The keys here MUST be the
        ``content_hash`` values from ``embeddings``; the indexer Job's
        ``vs_upsert`` task constructs this map from the
        :class:`SourceProvenanceRow` ledger.
    index_name : str
        Fully-qualified Vector Search index name, e.g.,
        ``"brickvision.capability_graph.entity_index"``.
    batch_size, started_at_ms, completed_at_ms, sleep
        Same shape as :func:`embed.embed_batch`.

    Returns
    -------
    VsUpsertResult
        Errors captured per batch. The indexer's ``promote`` task
        treats ANY error as a hard fail (zero tolerance: a partial
        vector index produces silently-wrong retrieval results, which
        is strictly worse than retrieving against the prior snapshot).
    """

    rows_upserted = 0
    batches_attempted = 0
    batches_succeeded = 0
    retries_total = 0
    errors: list[VsUpsertError] = []
    dry_run = _is_dry_run()
    dry_run_batches: list[dict[str, Any]] = []

    if not embeddings:
        end = completed_at_ms if completed_at_ms is not None else started_at_ms
        if dry_run:
            _log_dry_run_batches(index_name=index_name, batches=dry_run_batches)
        return VsUpsertResult(
            rows_upserted=0, batches_attempted=0, batches_succeeded=0,
            retries=0, errors=(), started_at_ms=started_at_ms,
            completed_at_ms=end, duration_ms=max(0, end - started_at_ms),
            dry_run=dry_run,
        )

    # Prepare per-row tuples once: drop entries whose content_hash
    # has no metadata (defensive; the indexer Job task should have
    # filtered these out, but we double-check).
    prepared: list[tuple[str, Sequence[float], Mapping[str, Any]]] = []
    for emb in embeddings:
        meta = entity_metadata.get(emb.content_hash)
        if meta is None:
            continue
        prepared.append((emb.content_hash, emb.embedding, meta))

    # Batch and dispatch.
    for batch_index in range(0, len(prepared), batch_size):
        batch = prepared[batch_index : batch_index + batch_size]
        batches_attempted += 1

        primary_keys = [pk for pk, _v, _m in batch]
        vectors = [list(v) for _pk, v, _m in batch]
        metadata = [m for _pk, _v, m in batch]

        if dry_run:
            dry_run_batches.append(
                {
                    "batch_index": batch_index // batch_size,
                    "row_count": len(batch),
                    "primary_keys": list(primary_keys),
                    "embedding_lengths": [len(v) for v in vectors],
                    "metadata": [dict(m) for m in metadata],
                }
            )
            rows_upserted += len(batch)
            batches_succeeded += 1
            continue

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= _MAX_RETRIES:
            try:
                upserted = _upsert_via_vs_sdk(
                    index_name=index_name,
                    primary_keys=primary_keys,
                    embeddings=vectors,
                    metadata=metadata,
                )
                rows_upserted += upserted
                batches_succeeded += 1
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 — defensive
                last_exc = exc
                attempt += 1
                if attempt > _MAX_RETRIES:
                    break
                retries_total += 1
                sleep(_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))

        if last_exc is not None:
            errors.append(
                VsUpsertError(
                    batch_index=batch_index // batch_size,
                    primary_keys=tuple(primary_keys),
                    error_kind=type(last_exc).__name__,
                    error_message=str(last_exc),
                )
            )

    if dry_run:
        _log_dry_run_batches(index_name=index_name, batches=dry_run_batches)

    end = completed_at_ms if completed_at_ms is not None else started_at_ms
    return VsUpsertResult(
        rows_upserted=rows_upserted,
        batches_attempted=batches_attempted,
        batches_succeeded=batches_succeeded,
        retries=retries_total,
        errors=tuple(errors),
        started_at_ms=started_at_ms,
        completed_at_ms=end,
        duration_ms=max(0, end - started_at_ms),
        dry_run=dry_run,
    )


__all__ = [
    "VsUpsertError",
    "VsUpsertResult",
    "vs_upsert_embeddings",
]
