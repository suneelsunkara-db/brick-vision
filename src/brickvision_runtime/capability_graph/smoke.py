"""Indexer task 11 — smoke-test the just-built snapshot against the
locked baseline (per §23.3.3).

Reads the 5 :class:`SmokeBaselineRow` rows from
``<BV_CATALOG>.<BV_SCHEMA>.smoke_baseline`` (a closed table — locked at
v0.7.7 ship and updated only via deliberate engineering review),
issues each ``query_text`` against the just-upserted Vector Search
index, and verifies that ``expected_top_1_extension_id`` appears as
the top-1 result on at least :attr:`baseline_hit_rate` of the queries.

Why the baseline lives in Delta (not in code)
=============================================

§23.3.3 explicitly carves the baseline out as data-not-code: the 5
queries + expected results are reviewable, queryable, and
versionable independently of the indexer source. The indexer Job's
``smoke`` task reads this table at run-time, so adjusting the
acceptance threshold is a SQL UPDATE plus a code-review trail —
not a deploy.

Why the gate is **per-query top-1** rather than aggregate hit rate
==================================================================

Two reasons:

  1. **Detectability.** A 4/5 hit rate (80%) on a 5-query baseline
     is statistically indistinguishable from random pass/fail
     fluctuation; ranking the failure-cases by ``query_id`` lets the
     reviewer see exactly which query regressed and inspect its
     ranked candidates in the index.
  2. **Authority sensitivity.** The 5 baselines are deliberately
     drawn from different source authorities (SDK, OpenAPI, docs,
     hand-authored exemplar, blog) — if a particular source's
     authority composition broke, the failure profile points at
     the source, not at the merge logic.

The aggregate ``hit_rate`` is still computed and surfaced for the
``promote`` task's gate check.

Aggregate gate semantics
========================

Per §23.3.3:
  * ``observed_hit_rate >= baseline_hit_rate`` → smoke passes; the
    snapshot is eligible for promotion.
  * ``observed_hit_rate < baseline_hit_rate`` → smoke fails;
    :mod:`promote` refuses the active-snapshot flip and the indexer
    surfaces :data:`ReasonCode.CAPABILITY_GRAPH_SMOKE_FAILED`.

The baseline is a **floor**, not a target — equality passes. The
locked baseline rate at v0.7.7 ship is 0.80 (4/5 queries must hit
top-1) per §23.3.3 — see the SmokeBaselineRow seed data.

Discipline rule 15 (N189) — production-only retrieval
=====================================================

This module previously declared a ``VectorSearchRetriever(Protocol)``
and accepted a ``retriever`` parameter so offline tests could inject
a stub. Per [`docs/01-overview.md`](
../../../../docs/01-overview.md) §0 +
[`docs/10-generation-philosophy.md`](
../../../../docs/10-generation-philosophy.md) §8.6 that Protocol seam
was retired. The production code path now calls
:class:`databricks.vector_search.client.VectorSearchClient`
directly (lazy-imported); the ``BV_FAKE_LLM=true`` env-gate
short-circuits the SDK call by returning canned per-query top-1
hits from
``tests/fixtures/capability_graph/canned_smoke_hits.json``
(override via ``BV_FAKE_LLM_SMOKE_HITS_FIXTURE``). When no canned
hit is configured for a baseline ``query_id``, the function
defaults to the row's ``expected_top_1_extension_id`` so the
smoke gate passes by construction in the offline harness.

Reason codes
============

Per §23.3.3:
  * :data:`ReasonCode.CAPABILITY_GRAPH_SMOKE_FAILED` — emitted when
    ``observed_hit_rate < baseline_hit_rate``; the failing
    ``query_id`` list is logged for triage. Surfaced to the indexer
    Job as a non-zero exit on the smoke task.
  * :data:`ReasonCode.CAPABILITY_GRAPH_SMOKE_BASELINE_EMPTY` —
    emitted when the baseline table contains zero rows; this is a
    misconfiguration (someone TRUNCATEd the baseline) and the
    indexer refuses to promote an unverified snapshot.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path

from .schemas.types import SmokeBaselineRow


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SmokeQueryResult:
    """One per query: did the expected extension show up at top-1?"""

    query_id: str
    query_text: str
    expected_top_1_extension_id: str
    observed_top_1_extension_id: str | None
    """``None`` when the retriever returned an empty result list."""
    is_hit: bool
    """``True`` iff ``observed == expected`` exactly."""


@dataclasses.dataclass(frozen=True, slots=True)
class SmokeQueryError:
    """One per query that raised at retrieval time. Fatal to that
    query (counted as a miss) but not to the smoke run."""

    query_id: str
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class SmokeResult:
    """Aggregate output of one ``run_smoke`` invocation."""

    snapshot_id: str
    queries_run: int
    hits: int
    misses: int
    """Includes both retrieval errors AND wrong top-1."""
    observed_hit_rate: float
    baseline_hit_rate: float
    passed: bool
    """``observed_hit_rate >= baseline_hit_rate``."""
    per_query: tuple[SmokeQueryResult, ...]
    errors: tuple[SmokeQueryError, ...]
    started_at_ms: int
    completed_at_ms: int
    duration_ms: int


# ---------------------------------------------------------------------------
# Production retrieval — VectorSearchClient wrapper with BV_FAKE_LLM short-circuit
# ---------------------------------------------------------------------------


_DEFAULT_FAKE_HITS_FIXTURE = "tests/fixtures/capability_graph/canned_smoke_hits.json"


def _is_fake_llm() -> bool:
    return os.environ.get("BV_FAKE_LLM", "false").lower() in ("1", "true", "yes")


def _load_canned_smoke_hits() -> dict[str, list[str]]:
    raw = os.environ.get("BV_FAKE_LLM_SMOKE_HITS_FIXTURE", "").strip()
    fixture_path = Path(raw) if raw else Path(_DEFAULT_FAKE_HITS_FIXTURE)
    if not fixture_path.exists():
        return {}
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    canned: dict[str, list[str]] = {}
    for entry in payload.get("hits", []):
        qid = entry.get("query_id")
        ranked = entry.get("ranked_extension_ids", [])
        if isinstance(qid, str) and isinstance(ranked, list):
            canned[qid] = [str(x) for x in ranked]
    return canned


def _retrieve_top_extensions(
    *,
    query_text: str,
    snapshot_id: str,
    top_k: int,
    index_name: str,
) -> Sequence[str]:
    """Production VS retrieval.

    Lazy-imports :mod:`databricks.vector_search`; the smoke task
    asks for ``top_k=1`` so we ask VS for ``num_results=top_k`` and
    pull the ``id`` column out of the response.
    """

    from databricks.vector_search.client import VectorSearchClient  # noqa: PLC0415

    client = VectorSearchClient()
    index = client.get_index(index_name=index_name)
    response = index.similarity_search(
        query_text=query_text,
        columns=["id"],
        num_results=top_k,
        filters={"snapshot_id": snapshot_id},
    )
    rows = response.get("result", {}).get("data_array", []) or []
    return tuple(str(row[0]) for row in rows if row)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def run_smoke(
    *,
    baseline: Sequence[SmokeBaselineRow],
    snapshot_id: str,
    index_name: str,
    started_at_ms: int,
    completed_at_ms: int | None = None,
) -> SmokeResult:
    """Run the locked baseline against the just-built snapshot.

    Parameters
    ----------
    baseline : Sequence[SmokeBaselineRow]
        The 5 (typically) baseline rows read from
        ``<BV_CATALOG>.<BV_SCHEMA>.smoke_baseline``. Must be non-empty;
        an empty baseline causes ``passed=False`` with hit_rate=0
        (the indexer surfaces this as ``SMOKE_BASELINE_EMPTY``).
    snapshot_id : str
        The snapshot we're validating; passed through to Vector
        Search as a partition filter.
    index_name : str
        Fully-qualified Vector Search index name, e.g.,
        ``"brickvision.capability_graph.entity_index"``.
    started_at_ms, completed_at_ms : int
        For telemetry. ``completed_at_ms`` defaults to
        ``started_at_ms`` (caller passes a real wallclock).

    Returns
    -------
    SmokeResult
        ``passed`` is the gate the ``promote`` task reads. The
        per-query breakdown is preserved for triage and for surfacing
        in the Knowledge UI's `/api/knowledge/health` endpoint.

    Notes on rate computation
    -------------------------
    The denominator is ``len(baseline)``, not ``queries_run``: a
    retrieval error counts as a miss, NOT as "removed from the
    sample". This is deliberate — flaky retrieval IS a quality
    regression worth surfacing.

    Edge cases
    ----------
    * Empty baseline → ``observed_hit_rate = 0.0``, ``passed = False``,
      ``baseline_hit_rate = 0.0`` (defensive — caller's expected
      gate is :data:`ReasonCode.CAPABILITY_GRAPH_SMOKE_BASELINE_EMPTY`).
    * Multiple baseline rows with the same ``query_id`` → silently
      treated as separate queries (the closed-table CHECK
      constraint should prevent this, but the function doesn't
      enforce it).
    """

    if not baseline:
        end = completed_at_ms if completed_at_ms is not None else started_at_ms
        return SmokeResult(
            snapshot_id=snapshot_id,
            queries_run=0, hits=0, misses=0,
            observed_hit_rate=0.0, baseline_hit_rate=0.0,
            passed=False, per_query=(), errors=(),
            started_at_ms=started_at_ms, completed_at_ms=end,
            duration_ms=max(0, end - started_at_ms),
        )

    per_query: list[SmokeQueryResult] = []
    errors: list[SmokeQueryError] = []
    hits = 0

    fake_llm = _is_fake_llm()
    canned_hits = _load_canned_smoke_hits() if fake_llm else {}

    for row in baseline:
        try:
            if fake_llm:
                ranked = canned_hits.get(
                    row.query_id, [row.expected_top_1_extension_id]
                )
                top_ranked: Sequence[str] = tuple(ranked[:1])
            else:
                top_ranked = _retrieve_top_extensions(
                    query_text=row.query_text,
                    snapshot_id=snapshot_id,
                    top_k=1,
                    index_name=index_name,
                )
        except Exception as exc:  # noqa: BLE001 — defensive over generic SDK exceptions
            errors.append(
                SmokeQueryError(
                    query_id=row.query_id,
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            per_query.append(
                SmokeQueryResult(
                    query_id=row.query_id,
                    query_text=row.query_text,
                    expected_top_1_extension_id=row.expected_top_1_extension_id,
                    observed_top_1_extension_id=None,
                    is_hit=False,
                )
            )
            continue

        observed = top_ranked[0] if top_ranked else None
        is_hit = observed == row.expected_top_1_extension_id
        if is_hit:
            hits += 1

        per_query.append(
            SmokeQueryResult(
                query_id=row.query_id,
                query_text=row.query_text,
                expected_top_1_extension_id=row.expected_top_1_extension_id,
                observed_top_1_extension_id=observed,
                is_hit=is_hit,
            )
        )

    queries_run = len(baseline)
    misses = queries_run - hits
    observed_hit_rate = hits / queries_run if queries_run else 0.0

    # The baseline rate is the MIN across all rows — a regression on
    # any single query's expected rate flunks the snapshot, even if
    # other queries over-perform. (This protects against the
    # "average hides the outlier" failure mode.)
    baseline_hit_rate = min(row.baseline_hit_rate for row in baseline)

    passed = observed_hit_rate >= baseline_hit_rate

    end = completed_at_ms if completed_at_ms is not None else started_at_ms
    return SmokeResult(
        snapshot_id=snapshot_id,
        queries_run=queries_run,
        hits=hits, misses=misses,
        observed_hit_rate=observed_hit_rate,
        baseline_hit_rate=baseline_hit_rate,
        passed=passed,
        per_query=tuple(per_query),
        errors=tuple(errors),
        started_at_ms=started_at_ms, completed_at_ms=end,
        duration_ms=max(0, end - started_at_ms),
    )


__all__ = [
    "SmokeQueryError",
    "SmokeQueryResult",
    "SmokeResult",
    "run_smoke",
]
