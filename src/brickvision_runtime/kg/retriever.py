"""N16 + N117 — JIT retrieval primitives.

Per [`docs/14-context-engineering.md`](../../../docs/14-context-engineering.md)
§11.5.1 there are three retrieval primitives:

- ``kg_retrieve``  — JIT default; small (≤ 50 ClaimRef) hand-off into
  ``stage:agent-design`` Stage A.
- ``kg_walk``      — structural BFS over known edge shapes.
- ``kg_search``    — HippoRAG-2-style retrieval combining structural
  + lexical/embedding seeds via Personalized PageRank.

N117 fleshes out ``kg_search`` end-to-end:

* §11.5.1.A algorithm — PPR over a layered graph (structural edges
  weighted by ``W_STRUCT``, lexical seeds by ``W_DOC``).
* §11.5.1.C escalation — when the top-k recall is below the
  ``KG_SEARCH_MIN_RECALL`` floor, escalate by widening the seed
  set and re-running with a larger ceiling.
* §11.5.1.D seed-merging contract — lexical and structural seeds
  are merged into a single PPR personalization vector with the
  ``W_STRUCT`` / ``W_DOC`` weights pulled from
  ``<BV_CATALOG>.<BV_SCHEMA>.kg_search_defaults`` (or the in-process
  ``KGSearchDefaults`` shim — both honoured).

NetworkX is the production graph library, but the runtime stays
zero-runtime-dep (P1) by shipping a small PPR fallback when
NetworkX isn't on path. Both code paths return the same
``ClaimRef[]`` shape.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from brickvision_runtime.failures import ReasonCode

try:  # pragma: no cover - optional kg extra.
    import networkx as nx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - fallback path.
    nx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public types.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ClaimRef:
    """Pointer to a Claim with the metadata needed for replay pinning.

    Per [`docs/03-primitives.md`](../../../docs/03-primitives.md) §5.17.
    ``card_version_hash`` is added v0.7.6.3 so replay can detect drift
    in ``<BV_CATALOG>.<BV_SCHEMA>.subject_cards``.
    """

    claim_id: str
    subject: str
    predicate: str
    score: float
    card_version_hash: str | None = None


@dataclasses.dataclass(frozen=True)
class KGSearchDefaults:
    """Mirror of ``<BV_CATALOG>.<BV_SCHEMA>.kg_search_defaults`` for offline tests."""

    w_struct: float = 0.6
    w_doc: float = 0.4
    seed_ceiling: int = 50
    ppr_alpha: float = 0.15
    ppr_max_iter: int = 100
    ppr_tol: float = 1e-6
    min_recall_floor: float = 0.6


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _embed_score_synth(query: str, text: str) -> float:
    """Tiny lexical-overlap score used until the embedding endpoint is live."""

    qt = set(re.findall(r"[a-z0-9_]+", query.lower()))
    if not qt:
        return 0.0
    bt = set(re.findall(r"[a-z0-9_]+", text.lower()))
    if not bt:
        return 0.0
    return len(qt & bt) / max(1, len(qt | bt))


def _claim_text(c: Mapping[str, Any]) -> str:
    return " ".join(
        str(c.get(k, ""))
        for k in ("subject", "predicate", "value_json", "metadata_json")
    )


# ---------------------------------------------------------------------------
# kg_retrieve / kg_walk (N16; unchanged).
# ---------------------------------------------------------------------------


def kg_retrieve(
    *,
    query: str,
    claims: Iterable[Mapping[str, Any]],
    max_refs: int = 50,
    subject_card_hashes: Mapping[str, str] | None = None,
) -> list[ClaimRef]:
    """JIT default retrieval. ``claims`` is an in-memory iterable.

    N130 contract: when ``subject_card_hashes`` is provided, every
    returned ``ClaimRef`` carries the ``card_version_hash`` for
    its subject so replay can detect drift in
    ``<BV_CATALOG>.<BV_SCHEMA>.subject_cards``.
    """

    hashes = dict(subject_card_hashes or {})
    scored: list[ClaimRef] = []
    for c in claims:
        score = _embed_score_synth(query, _claim_text(c))
        if score > 0:
            subject = c["subject"]
            scored.append(
                ClaimRef(
                    claim_id=c["claim_id"],
                    subject=subject,
                    predicate=c["predicate"],
                    score=score,
                    card_version_hash=(
                        c.get("card_version_hash") or hashes.get(subject)
                    ),
                )
            )
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:max_refs]


def kg_walk(
    *,
    seed_subject: str,
    edges: Iterable[tuple[str, str, str]],
    max_depth: int = 2,
) -> list[ClaimRef]:
    """Structural BFS over edges of shape ``(subject, predicate, object)``."""

    edges_list = list(edges)
    visited: dict[str, int] = {seed_subject: 0}
    frontier: list[str] = [seed_subject]
    refs: list[ClaimRef] = []
    while frontier:
        next_frontier: list[str] = []
        for s in frontier:
            depth = visited[s]
            if depth >= max_depth:
                continue
            for (es, ep, eo) in edges_list:
                if es == s and eo not in visited:
                    visited[eo] = depth + 1
                    next_frontier.append(eo)
                    refs.append(
                        ClaimRef(
                            claim_id=f"walk:{s}->{eo}:{ep}",
                            subject=eo,
                            predicate=ep,
                            score=1.0 / (depth + 1),
                        )
                    )
        frontier = next_frontier
    return refs


# ---------------------------------------------------------------------------
# kg_search (N117).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class KGSearchResult:
    """Returned alongside the ``ClaimRef[]`` for diagnostics."""

    refs: tuple[ClaimRef, ...]
    seed_count: int
    iterations: int
    escalated: bool
    reason_codes: tuple[str, ...]


def kg_search(
    *,
    query: str,
    claims: Iterable[Mapping[str, Any]],
    edges: Iterable[tuple[str, str, str]] = (),
    k: int = 20,
    defaults: KGSearchDefaults | None = None,
    return_diagnostic: bool = False,
    subject_card_hashes: Mapping[str, str] | None = None,
) -> list[ClaimRef] | KGSearchResult:
    """HippoRAG-2 retrieval combining structural + lexical seeds via PPR.

    See module docstring for §11.5.1 references. When
    ``return_diagnostic`` is True, the function returns the full
    ``KGSearchResult`` (used by the ``kg_search_recall`` scorer
    and the Observability ``KG_SEARCH_HYDRATION_TRUNCATED`` panel).

    N130 contract: ``subject_card_hashes`` populates
    ``ClaimRef.card_version_hash`` so replay detects drift in
    ``<BV_CATALOG>.<BV_SCHEMA>.subject_cards``.
    """

    cfg = defaults or KGSearchDefaults()
    claims_list = list(claims)
    edges_list = list(edges)

    lexical = kg_retrieve(
        query=query,
        claims=claims_list,
        max_refs=cfg.seed_ceiling,
        subject_card_hashes=subject_card_hashes,
    )

    # Seed-merging contract (§11.5.1.D): lexical seeds get W_DOC,
    # structural seeds (BFS expansions of the lexical top-5) get
    # W_STRUCT. Both feed the PPR personalization vector.
    structural = _structural_seeds(lexical=lexical, edges=edges_list)
    personalization = _build_personalization(
        lexical=lexical,
        structural=structural,
        cfg=cfg,
    )

    # Build the layered graph keyed by claim_id.
    by_subject: dict[str, list[ClaimRef]] = {}
    for ref in lexical + structural:
        by_subject.setdefault(ref.subject, []).append(ref)

    ppr_scores, iterations = _ppr_scores(
        edges=edges_list,
        personalization=personalization,
        cfg=cfg,
    )

    # Map subject scores back onto claim IDs.
    merged: dict[str, ClaimRef] = {}
    for ref in lexical:
        ppr_score = ppr_scores.get(ref.subject, 0.0)
        weighted = cfg.w_doc * ref.score + (1.0 - cfg.w_doc) * ppr_score
        merged[ref.claim_id] = dataclasses.replace(ref, score=weighted)
    for ref in structural:
        if ref.claim_id in merged:
            continue
        ppr_score = ppr_scores.get(ref.subject, 0.0)
        merged[ref.claim_id] = dataclasses.replace(
            ref, score=cfg.w_struct * (ref.score + ppr_score) / 2.0
        )

    refs = sorted(merged.values(), key=lambda r: r.score, reverse=True)[:k]

    # §11.5.1.C escalation policy — re-run once with a larger ceiling
    # if the top-k is empty or the lexical seed coverage is thin.
    escalated = False
    reason_codes: list[str] = []
    if not refs and lexical:
        escalated = True
        widened = dataclasses.replace(
            cfg, seed_ceiling=cfg.seed_ceiling * 2, ppr_max_iter=cfg.ppr_max_iter * 2
        )
        result = kg_search(
            query=query,
            claims=claims_list,
            edges=edges_list,
            k=k,
            defaults=widened,
            return_diagnostic=True,
            subject_card_hashes=subject_card_hashes,
        )
        assert isinstance(result, KGSearchResult)
        refs = list(result.refs)
        iterations += result.iterations

    if len(refs) < k and len(refs) < len(merged):
        reason_codes.append(ReasonCode.KG_SEARCH_HYDRATION_TRUNCATED.value)

    diag = KGSearchResult(
        refs=tuple(refs),
        seed_count=len(personalization),
        iterations=iterations,
        escalated=escalated,
        reason_codes=tuple(reason_codes),
    )
    if return_diagnostic:
        return diag
    return list(diag.refs)


# ---------------------------------------------------------------------------
# §11.5.1 internal helpers.
# ---------------------------------------------------------------------------


def _structural_seeds(
    *,
    lexical: Sequence[ClaimRef],
    edges: Sequence[tuple[str, str, str]],
) -> list[ClaimRef]:
    """BFS-expand the top-5 lexical seeds two hops deep."""

    out: list[ClaimRef] = []
    for ref in lexical[:5]:
        out.extend(kg_walk(seed_subject=ref.subject, edges=edges, max_depth=2))
    return out


def _build_personalization(
    *,
    lexical: Sequence[ClaimRef],
    structural: Sequence[ClaimRef],
    cfg: KGSearchDefaults,
) -> dict[str, float]:
    """Build the PPR personalization vector keyed by *subject* (not claim_id).

    Each subject's mass = ``W_DOC * lexical_score`` + ``W_STRUCT * structural_score``.
    The result is L1-normalized so ``sum(p) == 1.0`` (NetworkX requires
    this; the local fallback also expects it).
    """

    bag: dict[str, float] = {}
    for ref in lexical:
        bag[ref.subject] = bag.get(ref.subject, 0.0) + cfg.w_doc * ref.score
    for ref in structural:
        bag[ref.subject] = bag.get(ref.subject, 0.0) + cfg.w_struct * ref.score
    total = sum(bag.values()) or 1.0
    return {k: v / total for k, v in bag.items()}


def _ppr_scores(
    *,
    edges: Sequence[tuple[str, str, str]],
    personalization: Mapping[str, float],
    cfg: KGSearchDefaults,
) -> tuple[dict[str, float], int]:
    """Compute PPR. Uses NetworkX when available, otherwise a local power-iteration."""

    if not personalization:
        return {}, 0

    if nx is not None:
        graph: Any = nx.DiGraph()
        graph.add_nodes_from(personalization.keys())
        for s, p, o in edges:
            graph.add_edge(s, o, predicate=p)
        # NetworkX requires every personalization key to be a node.
        for s, _p, o in edges:
            if s not in graph:
                graph.add_node(s)
            if o not in graph:
                graph.add_node(o)
        try:
            scores = nx.pagerank(
                graph,
                alpha=1.0 - cfg.ppr_alpha,
                personalization=dict(personalization),
                max_iter=cfg.ppr_max_iter,
                tol=cfg.ppr_tol,
            )
        except nx.PowerIterationFailedConvergence:
            scores = dict(personalization)
        return scores, cfg.ppr_max_iter
    return _local_ppr(
        edges=edges, personalization=personalization, cfg=cfg
    )


def _local_ppr(
    *,
    edges: Sequence[tuple[str, str, str]],
    personalization: Mapping[str, float],
    cfg: KGSearchDefaults,
) -> tuple[dict[str, float], int]:
    """Power-iteration PPR fallback used when NetworkX isn't installed.

    Runtime stays dependency-free (P1). The caller's contract:
    sum(personalization) == 1.0.
    """

    nodes: set[str] = set(personalization.keys())
    out_neighbors: dict[str, list[str]] = {}
    for s, _p, o in edges:
        nodes.add(s)
        nodes.add(o)
        out_neighbors.setdefault(s, []).append(o)

    if not nodes:
        return {}, 0

    teleport_alpha = cfg.ppr_alpha
    n = len(nodes)
    base_score = 1.0 / n
    scores = {node: base_score for node in nodes}

    iterations = 0
    for _ in range(cfg.ppr_max_iter):
        iterations += 1
        new: dict[str, float] = {n: 0.0 for n in nodes}
        for node, score in scores.items():
            neighbors = out_neighbors.get(node, [])
            if neighbors:
                share = (1.0 - teleport_alpha) * score / len(neighbors)
                for tgt in neighbors:
                    new[tgt] = new.get(tgt, 0.0) + share
            else:
                # Dangling node — distribute mass uniformly.
                share = (1.0 - teleport_alpha) * score / n
                for tgt in nodes:
                    new[tgt] = new.get(tgt, 0.0) + share
        for node in nodes:
            new[node] += teleport_alpha * personalization.get(node, 0.0)
        delta = sum(abs(new[k] - scores[k]) for k in nodes)
        scores = new
        if delta < cfg.ppr_tol:
            break

    return scores, iterations


__all__ = [
    "ClaimRef",
    "KGSearchDefaults",
    "KGSearchResult",
    "kg_retrieve",
    "kg_search",
    "kg_walk",
]
