"""Indexer task 8 — embedding generation via Foundation Model Serving
(per §23.3.5).

Takes the chunks, methods, operations, and other text-bearing entities
produced by the adapters + graph_builder, and produces
:class:`EmbeddingCacheRow` rows by calling the configured
``LLM_EMBEDDING_TASKS`` endpoint. The embeddings are then handed off to
:mod:`vs_upsert` which writes them into Mosaic AI Vector Search.

Why content-hash caching is the central design choice
=====================================================

Most v0.7.7 daily refreshes are **incremental**: ~5% of the corpus
changes day-over-day (per §23.3.6), so 95% of texts have a stable
``content_hash`` and don't need re-embedding. The
:class:`EmbeddingCacheRow` table at
``<BV_CATALOG>.<BV_SCHEMA>.embedding_cache`` is a content-hash → vector
ledger; this module's job is to:

  1. Look up each request's ``content_hash`` against the cache.
  2. Skip the network call if there's a hit (the same exact text was
     embedded in a prior snapshot).
  3. Batch the misses 128-at-a-time to FMS, retry on rate-limits, and
     write the new embeddings back into the cache.

§23.3.5 targets ~90% cache hit rate on a typical day; with ~60K total
texts that means ~6K misses per refresh × $0.0001/text ≈ $0.60/day.

Discipline rule 15 (N189) — production-only embedding
=====================================================

This module previously declared an ``EmbeddingClient(Protocol)`` and
accepted a ``client`` parameter on :func:`embed_batch` so offline
tests could inject a stub returning deterministic vectors. Per
[`docs/01-overview.md`](../../../../docs/01-overview.md) §0 +
[`docs/10-generation-philosophy.md`](
../../../../docs/10-generation-philosophy.md) §8.6 that Protocol seam
was retired. The production code path now calls Mosaic AI Foundation
Model Serving directly via :mod:`databricks.sdk` (lazy-imported); the
``BV_FAKE_LLM=true`` env-gate (per [`docs/19-local-development.md`](
../../../../docs/19-local-development.md) §15.2.1) short-circuits the
network call by reading canned vectors from
``tests/fixtures/capability_graph/canned_embeddings.json`` (override
the path via ``BV_FAKE_LLM_EMBEDDINGS_FIXTURE``).

Reason codes
============

Per §23.3.5:
  * :data:`ReasonCode.CAPABILITY_GRAPH_EMBEDDING_BUDGET_EXCEEDED` —
    raised mid-batch when ``daily_budget_usd`` would be breached;
    the in-flight EmbedResult ships the partial work and the
    indexer's ``embed`` task fails the snapshot.
  * :data:`ReasonCode.CAPABILITY_GRAPH_EMBEDDING_TOKEN_CAP_EXCEEDED` —
    same shape, but for ``daily_token_cap``.
  * :data:`ReasonCode.CAPABILITY_GRAPH_EMBEDDING_ENDPOINT_ERROR` —
    the FMS endpoint returned a non-retryable error or exhausted
    retries; surfaced per-request in :attr:`EmbedResult.errors`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from brickvision_runtime.observability import record_model_invocation

from .schemas.types import EmbeddingCacheRow


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class EmbedRequest:
    """One text-to-embed request keyed by the caller's content_hash.

    The ``content_hash`` is the cache key — it must be the same hash
    the source adapters emit (sha256[:16] of canonical content) so a
    chunk indexed in snapshot N+1 hits the same row as in snapshot N.
    """

    content_hash: str
    text: str


@dataclasses.dataclass(frozen=True, slots=True)
class EmbedError:
    """Per-request failure; non-fatal — sibling requests in the batch
    continue."""

    content_hash: str
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class EmbedResult:
    """Aggregate output of one ``embed_batch`` invocation."""

    rows: tuple[EmbeddingCacheRow, ...]  # cache hits + new misses
    cache_hits: int
    cache_misses: int
    network_calls: int  # batches actually sent to FMS
    estimated_token_count: int
    estimated_cost_usd: float
    retries: int  # total backoff retries across all batches
    errors: tuple[EmbedError, ...]
    truncated_at_request_index: int | None
    """Index into the input requests at which we stopped due to
    budget exhaustion (``None`` when the full input was processed)."""


# ---------------------------------------------------------------------------
# Defaults (per §23.3.5)
# ---------------------------------------------------------------------------


_DEFAULT_EMBEDDING_ENDPOINT: str = "databricks-qwen3-embedding-0-6b"
_DEFAULT_EMBEDDING_DIM: int = 1024
_DEFAULT_BATCH_SIZE: int = 128
_DEFAULT_COST_PER_1K_TOKENS_USD: float = 0.0001
"""Default embedding endpoint for LLM_EMBEDDING_TASKS."""

_MAX_RETRIES: int = 3
_RETRY_BASE_DELAY_S: float = 0.25  # 0.25, 0.5, 1.0 (exp backoff factor 2)


_DEFAULT_CANNED_EMBEDDINGS_FIXTURE = (
    "tests/fixtures/capability_graph/canned_embeddings.json"
)


def _approx_token_count(text: str) -> int:
    """Word-count proxy for a single string.

    Matches the chunker's proxy in :mod:`docs_adapter` for consistency:
    ``words / 0.75 ≈ tokens``. Replaced by tiktoken when the indexer
    Job ships with the optional ``tiktoken`` dependency.
    """

    word_count = len(text.split())
    return max(1, int(round(word_count / 0.75)))


def _chunked(seq: Sequence[EmbedRequest], n: int) -> Iterable[list[EmbedRequest]]:
    """Split ``seq`` into batches of ``n``."""

    for i in range(0, len(seq), n):
        yield list(seq[i : i + n])


# ---------------------------------------------------------------------------
# FMS HTTP wrapper — production code path with BV_FAKE_LLM short-circuit
# ---------------------------------------------------------------------------


def _is_fake_llm() -> bool:
    return os.environ.get("BV_FAKE_LLM", "false").lower() in ("1", "true", "yes")


def _resolve_canned_embeddings_path() -> Path:
    raw = os.environ.get("BV_FAKE_LLM_EMBEDDINGS_FIXTURE", "").strip()
    return Path(raw) if raw else Path(_DEFAULT_CANNED_EMBEDDINGS_FIXTURE)


def _text_fingerprint(text: str) -> str:
    """SHA-256 prefix used as the canned-fixture key."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _synthetic_vector(text: str, *, embedding_dim: int) -> list[float]:
    """Deterministic synthetic vector for ``BV_FAKE_LLM`` callers.

    When the canned-embeddings fixture has no entry for a given text,
    we build a sinusoidal vector seeded by ``sha256(text)[:16]`` so
    smoke tests are reproducible without needing a manually-curated
    entry per chunk. The vectors do not match the configured embedding
    space — they exist purely to exercise the surrounding pipeline code
    in offline mode.
    """

    seed = int.from_bytes(
        hashlib.sha256(text.encode("utf-8")).digest()[:8], "big", signed=False
    )
    out: list[float] = []
    for i in range(embedding_dim):
        # Mix the seed with the dimension index so different texts
        # produce non-aligned vectors. Normalize to roughly unit-norm.
        x = math.sin(((seed % 1_000_003) + i) * 0.001 + i * 0.017)
        out.append(round(float(x) * 0.05, 6))
    return out


def _load_canned_embeddings() -> tuple[dict[str, list[float]], int]:
    """Read the canned-embeddings fixture; returns (map, embedding_dim)."""

    path = _resolve_canned_embeddings_path()
    if not path.exists():
        return {}, _DEFAULT_EMBEDDING_DIM
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0":
        return {}, _DEFAULT_EMBEDDING_DIM
    embedding_dim = int(raw.get("embedding_dim", _DEFAULT_EMBEDDING_DIM))
    raw_map = raw.get("embeddings") or {}
    out: dict[str, list[float]] = {}
    for key, vec in raw_map.items():
        try:
            out[str(key)] = [float(x) for x in vec]
        except (TypeError, ValueError):
            continue
    return out, embedding_dim


def _embed_via_fms(
    *, texts: Sequence[str], endpoint: str, embedding_dim: int,
) -> Sequence[Sequence[float]]:
    """Production embedding call — Mosaic AI Foundation Model Serving.

    On ``BV_FAKE_LLM=true`` short-circuits to canned vectors from the
    fixture file (or a deterministic synthetic vector when the
    fixture has no entry). Live path lazy-imports
    :class:`databricks.sdk.WorkspaceClient` and calls
    ``serving_endpoints.query()`` against ``endpoint``.
    """

    if _is_fake_llm():
        canned, fixture_dim = _load_canned_embeddings()
        used_dim = fixture_dim if canned else embedding_dim
        out: list[list[float]] = []
        for text in texts:
            vec = canned.get(_text_fingerprint(text))
            if vec is None:
                vec = _synthetic_vector(text, embedding_dim=used_dim)
            out.append(vec)
        return out

    from databricks.sdk import WorkspaceClient  # noqa: PLC0415

    if not hasattr(_embed_via_fms, "_client"):
        _embed_via_fms._client = WorkspaceClient()  # type: ignore[attr-defined]
    response = _embed_via_fms._client.serving_endpoints.query(  # type: ignore[attr-defined]
        name=endpoint,
        input=list(texts),
    )
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data") or []
    if data is None:
        data = []
    return [list(row.embedding) for row in data]


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def embed_batch(
    *,
    requests: Sequence[EmbedRequest],
    cache_lookup: Callable[[str], EmbeddingCacheRow | None],
    embedding_endpoint: str = _DEFAULT_EMBEDDING_ENDPOINT,
    embedding_dim: int = _DEFAULT_EMBEDDING_DIM,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    daily_token_cap: int | None = None,
    daily_budget_usd: float | None = None,
    cost_per_1k_tokens_usd: float = _DEFAULT_COST_PER_1K_TOKENS_USD,
    parsed_at_ms: int,
    sleep: Callable[[float], None] = lambda _s: None,
) -> EmbedResult:
    """Embed N requests using cache + FMS, returning EmbeddingCacheRows.

    Behavior:
      1. Linear scan of ``requests``; each is looked up in the cache.
      2. Cache misses accumulate into a batch buffer; when the buffer
         reaches ``batch_size``, the batch is sent to FMS via
         :func:`_embed_via_fms`.
      3. After each batch, the cumulative cost / token estimate is
         compared against ``daily_budget_usd`` / ``daily_token_cap``;
         if exceeded, the loop short-circuits and returns the partial
         result with ``truncated_at_request_index`` set.
      4. Retries on FMS exceptions: 3 attempts with exponential
         backoff (0.25s, 0.5s, 1.0s); after the third failure, every
         request in that batch is surfaced as an :class:`EmbedError`.

    The ``cache_lookup`` callable is supplied by the caller (typically
    a Spark-backed Delta lookup); it is a regular function parameter
    rather than a Protocol seam. The ``sleep`` parameter is the back-
    off injection point — tests pass a no-op; production passes
    :func:`time.sleep`.
    """

    rows: list[EmbeddingCacheRow] = []
    errors: list[EmbedError] = []
    cache_hits = 0
    cache_misses = 0
    network_calls = 0
    retries_total = 0
    cumulative_tokens = 0
    cumulative_cost_usd = 0.0
    truncated_at: int | None = None

    pending: list[tuple[int, EmbedRequest]] = []  # (request_index, request)

    def _flush() -> bool:
        """Send the pending buffer to FMS. Returns True on success
        (or graceful per-request errors); False when budget is hit
        and the caller should break."""

        nonlocal network_calls, retries_total, cumulative_tokens, cumulative_cost_usd

        if not pending:
            return True

        texts = [req.text for _, req in pending]
        batch_tokens = sum(_approx_token_count(t) for t in texts)
        batch_cost = (batch_tokens / 1000.0) * cost_per_1k_tokens_usd

        if daily_token_cap is not None and (cumulative_tokens + batch_tokens) > daily_token_cap:
            return False
        if daily_budget_usd is not None and (cumulative_cost_usd + batch_cost) > daily_budget_usd:
            return False

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= _MAX_RETRIES:
            model_started_at_ms = int(time.time() * 1000)
            try:
                vectors = _embed_via_fms(
                    texts=texts,
                    endpoint=embedding_endpoint,
                    embedding_dim=embedding_dim,
                )
                if len(vectors) != len(texts):
                    raise ValueError(
                        f"FMS returned {len(vectors)} vectors for {len(texts)} texts"
                    )
                for (_idx, req), vec in zip(pending, vectors):
                    if len(vec) != embedding_dim:
                        errors.append(
                            EmbedError(
                                content_hash=req.content_hash,
                                error_kind="DimensionMismatch",
                                error_message=(
                                    f"vector dim {len(vec)} != expected {embedding_dim}"
                                ),
                            )
                        )
                        continue
                    rows.append(
                        EmbeddingCacheRow(
                            content_hash=req.content_hash,
                            embedding_endpoint=embedding_endpoint,
                            embedding_dim=embedding_dim,
                            embedding=tuple(float(x) for x in vec),
                            emitted_at_ms=parsed_at_ms,
                            last_used_at_ms=parsed_at_ms,
                        )
                    )
                network_calls += 1
                cumulative_tokens += batch_tokens
                cumulative_cost_usd += batch_cost
                record_model_invocation(
                    feature="indexer_embedding",
                    model_role="embedding_tasks",
                    endpoint=embedding_endpoint,
                    request_kind="embedding",
                    status="succeeded",
                    started_at_ms=model_started_at_ms,
                    input_tokens=batch_tokens,
                    total_tokens=batch_tokens,
                    metadata={
                        "batch_size": len(texts),
                        "embedding_dim": embedding_dim,
                        "estimated_cost_usd": batch_cost,
                    },
                )
                pending.clear()
                return True
            except Exception as exc:  # noqa: BLE001 — defensive over generic SDK exceptions
                last_exc = exc
                attempt += 1
                if attempt > _MAX_RETRIES:
                    break
                retries_total += 1
                sleep(_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))

        # All retries exhausted — surface every pending request as an error.
        record_model_invocation(
            feature="indexer_embedding",
            model_role="embedding_tasks",
            endpoint=embedding_endpoint,
            request_kind="embedding",
            status="failed",
            started_at_ms=int(time.time() * 1000),
            error=last_exc,
            input_tokens=batch_tokens,
            total_tokens=batch_tokens,
            metadata={
                "batch_size": len(texts),
                "embedding_dim": embedding_dim,
                "estimated_cost_usd": batch_cost,
                "retries": _MAX_RETRIES,
            },
        )
        for _idx, req in pending:
            errors.append(
                EmbedError(
                    content_hash=req.content_hash,
                    error_kind=type(last_exc).__name__ if last_exc else "Unknown",
                    error_message=str(last_exc) if last_exc else "embed_via_fms failed",
                )
            )
        pending.clear()
        return True  # not a budget breach; we keep going

    for i, req in enumerate(requests):
        cached = cache_lookup(req.content_hash)
        if cached is not None:
            cache_hits += 1
            # Bump last_used_at_ms (used by retention to keep hot
            # entries; cold entries get GC'd after 30 days).
            rows.append(
                dataclasses.replace(cached, last_used_at_ms=parsed_at_ms)
            )
            continue

        cache_misses += 1
        pending.append((i, req))

        if len(pending) >= batch_size:
            ok = _flush()
            if not ok:
                truncated_at = i
                break

    if pending and truncated_at is None:
        ok = _flush()
        if not ok:
            truncated_at = len(requests) - len(pending)

    return EmbedResult(
        rows=tuple(rows),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        network_calls=network_calls,
        estimated_token_count=cumulative_tokens,
        estimated_cost_usd=cumulative_cost_usd,
        retries=retries_total,
        errors=tuple(errors),
        truncated_at_request_index=truncated_at,
    )


__all__ = [
    "EmbedError",
    "EmbedRequest",
    "EmbedResult",
    "embed_batch",
]
