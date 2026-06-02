"""Capability-graph read paths (v0.7.7).

Two public entry points feed the v0.7.7 user-facing surfaces:

1. ``list_extensions_with_exemplars(*, user_id)`` — backs the
   ``/api/skills`` (legacy) and ``/api/knowledge/extensions`` (new)
   endpoints. Returns rows from ``<BV_CATALOG>.<BV_SCHEMA>.extensions``
   where ``has_exemplar=true``, projected onto the
   ``SkillCatalogEntry`` shape consumed by ``apps/console`` until the
   dedicated ``/knowledge`` UI takes over.

2. ``kg_search_dual_substrate(*, query, ...)`` — Stage A retrieval
   contract change per ``docs/14-context-engineering.md`` §11.5.1.A
   (extended to dual-substrate v0.7.7). Walks the capability graph
   (primary substrate, source-of-truth for "what Databricks does")
   AND the workspace KG (secondary substrate, source-of-truth for
   "what's in the partner's workspace"), then arbitrates by
   ``source_authority``. Surfaces ``CAPABILITY_WORKSPACE_MISMATCH``
   as a Question when the two substrates contradict.

``list_extensions_with_exemplars`` is still a SHELL stub (it returns
empty until the install runner provisions the
``<bv>.capability_graph.*`` Tier B Delta tables and the indexer Job
populates them); ``kg_search_dual_substrate`` is **C.1 BULK** as of
N173: it runs a single joint PPR walk over the merged seed set with
per-substrate authority arbitration, detects predicate-level
disagreements between the two substrates, and emits
``CAPABILITY_WORKSPACE_MISMATCH`` into ``reason_codes`` when the
two contradict. It still degrades cleanly to single-substrate
``kg_search`` when no capability claims are supplied (preserving
pre-v0.7.7 Stage A behaviour bit-for-bit; this is the contract pinned
by ``tests/unit/test_self_bootstrap_capability_graph.py``).
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from brickvision_runtime.failures import ReasonCode
from brickvision_runtime.kg.retriever import (
    ClaimRef,
    KGSearchDefaults,
    KGSearchResult,
    _build_personalization,
    _ppr_scores,
    _structural_seeds,
    kg_retrieve,
    kg_search,
)


# ---------------------------------------------------------------------------
# Authority defaults — mirror the locked weights in
# ``<BV_CATALOG>.<BV_SCHEMA>.source_authority`` (per
# ``docs/23-databricks-capability-graph.md`` §23.1 + the
# ``SourceAuthorityAssignment()`` scorer's ``_LOCKED_AUTHORITY_WEIGHTS``).
#
# Workspace KG seeds always weight at fixed 1.0 (workspace facts are
# factual about the partner's environment); capability-graph seeds
# weight by their per-source authority. When a capability claim
# carries an explicit ``authority_weight`` field on the row dict,
# that value is used verbatim (this is the field the indexer's
# ``persist_to_uc`` task hydrates from
# ``<BV_CATALOG>.<BV_SCHEMA>.source_provenance``); otherwise the
# default below applies.
# ---------------------------------------------------------------------------

_WORKSPACE_AUTHORITY: float = 1.0
_DEFAULT_CAPABILITY_AUTHORITY: float = 0.85  # docs default if a row is missing the field


@dataclasses.dataclass(frozen=True)
class CapabilityGraphSnapshot:
    """Pointer to a frozen capability-graph snapshot for replay.

    The 6th replay pin (``capability_graph_snapshot_id``) carries
    this ``snapshot_id`` so a replay re-resolves Stage A retrieval
    against exactly the graph state used at the historical run.
    """

    snapshot_id: str
    promoted_at_ms: int
    is_active: bool
    sources_complete: tuple[str, ...]
    sources_partial: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class DualSubstrateResult:
    """Result of ``kg_search_dual_substrate``.

    ``capability_refs`` are extensions / methods drawn from the
    capability graph (what Databricks offers); ``workspace_refs``
    are claims drawn from the partner's workspace KG (what they
    have). When the two contradict, ``mismatch_subjects`` lists the
    affected subject IDs and ``CAPABILITY_WORKSPACE_MISMATCH`` is
    appended to ``reason_codes`` so the build pipeline pauses for
    HITL triage per ``docs/05-build-pipeline.md`` §7.6.
    """

    capability_refs: tuple[ClaimRef, ...]
    workspace_refs: tuple[ClaimRef, ...]
    mismatch_subjects: tuple[str, ...]
    snapshot_id: str | None
    reason_codes: tuple[str, ...]


_ACTIVE_SNAPSHOT_CACHE: tuple[float, CapabilityGraphSnapshot | None] | None = None
"""Module-level TTL cache for ``_active_snapshot()``.

Stage A retrieval calls ``_active_snapshot()`` once per
``kg_search_dual_substrate`` invocation, so the cache amortises the
UC query cost across repeated retrievals. TTL is configured via
``BV_CG_ACTIVE_SNAPSHOT_TTL_SEC`` (default 60s); a non-positive value
disables the cache.
"""


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "").lower() == "true"


def _resolve_dry_run_active_path() -> Path:
    """Path to the dry-run active-snapshot fixture.

    Default: ``tests/fixtures/capability_graph/active_snapshot.json``
    relative to the repo root. Overridable via
    ``BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH`` for test isolation.
    """

    override = os.environ.get("BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[3]
    return (
        repo_root
        / "tests"
        / "fixtures"
        / "capability_graph"
        / "active_snapshot.json"
    )


def _resolve_catalog() -> str:
    return os.environ.get("BV_CATALOG", "brickvision")


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


def _resolve_warehouse_id() -> str | None:
    return (
        os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
    )


def _ttl_seconds() -> float:
    raw = os.environ.get("BV_CG_ACTIVE_SNAPSHOT_TTL_SEC", "60")
    try:
        return float(raw)
    except ValueError:
        return 60.0


def _read_dry_run_active_snapshot() -> CapabilityGraphSnapshot | None:
    """Load ``CapabilityGraphSnapshot`` from the dry-run JSON fixture.

    Schema (extra fields ignored):

    .. code-block:: json

        {
          "snapshot_id": "snap-2026-04-15T02-00-00Z",
          "promoted_at_ms": 1713148800000,
          "is_active": true,
          "sources_complete": ["sdk", "openapi", "docs", "labs", "blog"],
          "sources_partial": []
        }

    Returns ``None`` when the fixture is absent OR ``is_active`` is
    ``false`` — the latter lets a fixture explicitly model the
    "no active snapshot" state.
    """

    target = _resolve_dry_run_active_path()
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    if not payload.get("is_active", True):
        return None
    snapshot_id = payload.get("snapshot_id")
    promoted_at_ms = payload.get("promoted_at_ms")
    if not isinstance(snapshot_id, str) or not isinstance(
        promoted_at_ms, (int, float)
    ):
        return None
    sources_complete = tuple(
        str(s) for s in payload.get("sources_complete", ()) if isinstance(s, str)
    )
    sources_partial = tuple(
        str(s) for s in payload.get("sources_partial", ()) if isinstance(s, str)
    )
    return CapabilityGraphSnapshot(
        snapshot_id=snapshot_id,
        promoted_at_ms=int(promoted_at_ms),
        is_active=True,
        sources_complete=sources_complete,
        sources_partial=sources_partial,
    )


def _query_active_snapshot_via_uc() -> CapabilityGraphSnapshot | None:
    """Issue a UC Statement Execution against ``active_snapshot_id``.

    Returns ``None`` when:
      * ``BV_INDEXER_WAREHOUSE_ID`` (or ``BV_WAREHOUSE_ID``) is unset
        — there's no warehouse to target;
      * ``databricks.sdk`` import fails — production environments
        without the SDK installed (some CI / partner pre-install
        contexts);
      * the ``WorkspaceClient`` constructor or the statement
        execution itself raises — typically "no auth", "no
        workspace", "table not found" (the indexer hasn't run yet),
        all of which gracefully degrade to the C.1 SHELL behaviour.

    The ``try/except Exception`` is intentional and rule-15-compliant
    — it preserves the pre-v0.7.7 Stage A passthrough on any setup
    where the indexer hasn't run, never silently fabricates a
    snapshot, and surfaces real errors to operators only via the
    ``brickvision indexer status`` CLI (which inspects the same
    code path with ``BV_INDEXER_STATUS_VERBOSE=true`` for debugging).
    """

    warehouse_id = _resolve_warehouse_id()
    if not warehouse_id:
        return None

    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
        from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

        client = WorkspaceClient()
    except Exception:  # noqa: BLE001 — graceful degrade per docstring
        return None

    catalog = _resolve_catalog()
    schema = _resolve_schema()

    def _rows_for(statement: str) -> list[list[Any]] | None:
        try:
            response = client.statement_execution.execute_statement(
                statement=statement,
                warehouse_id=warehouse_id,
                wait_timeout="50s",
            )
            state = response.status.state if response.status else None
            if state != StatementState.SUCCEEDED:
                return None
            result = response.result
        except Exception:  # noqa: BLE001
            return None
        return getattr(result, "data_array", None) or []

    rows = _rows_for(
        "SELECT a.snapshot_id, a.promoted_at_ms, r.planned_sources, r.partial_sources"
        f" FROM {catalog}.{schema}.active_snapshot_id a"
        f" LEFT JOIN {catalog}.{schema}.refresh_plan r"
        " ON r.result_snapshot_id = a.snapshot_id"
        " WHERE a.singleton_key = 'singleton'"
        " ORDER BY r.planned_at_ms DESC LIMIT 1"
    )
    if rows is None:
        rows = _rows_for(
            "SELECT snapshot_id, promoted_at_ms, sources_complete, sources_partial"
            f" FROM {catalog}.{schema}.active_snapshot_id"
            " WHERE singleton_key = 'singleton'"
        )
    if not rows:
        return None
    row = rows[0]
    if len(row) < 4:
        return None
    snapshot_id = str(row[0]) if row[0] is not None else None
    promoted_at_ms_raw = row[1]
    sources_complete_raw = row[2]
    sources_partial_raw = row[3]
    if snapshot_id is None or promoted_at_ms_raw is None:
        return None
    try:
        promoted_at_ms = int(promoted_at_ms_raw)
    except (TypeError, ValueError):
        return None

    def _coerce_array(raw: object) -> tuple[str, ...]:
        if raw is None:
            return ()
        if isinstance(raw, (list, tuple)):
            return tuple(str(s) for s in raw if s is not None)
        if isinstance(raw, str):
            # Some Statement Execution result paths return arrays as
            # JSON strings; defensively decode.
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                return ()
            return _coerce_array(decoded)
        return ()

    return CapabilityGraphSnapshot(
        snapshot_id=snapshot_id,
        promoted_at_ms=promoted_at_ms,
        is_active=True,
        sources_complete=_coerce_array(sources_complete_raw),
        sources_partial=_coerce_array(sources_partial_raw),
    )


def _active_snapshot(*, force_refresh: bool = False) -> CapabilityGraphSnapshot | None:
    """Read ``<BV_CATALOG>.<BV_SCHEMA>.active_snapshot_id`` (singleton row).

    Production code path. Three branches:

    1. ``BV_DRY_RUN=true`` — read the JSON fixture at
       ``tests/fixtures/capability_graph/active_snapshot.json`` (override
       via ``BV_DRY_RUN_ACTIVE_SNAPSHOT_PATH``). Discipline rule 15:
       this is the only test-control surface; the production code
       path doesn't change.
    2. ``BV_INDEXER_WAREHOUSE_ID`` unset OR UC query fails — return
       ``None`` (graceful degradation; preserves the C.1 SHELL
       passthrough on partner installs that haven't yet run the
       indexer).
    3. Production path — issue a Statement Execution against the
       partner's UC and project the row onto
       :class:`CapabilityGraphSnapshot`.

    Cached at the module level for ``BV_CG_ACTIVE_SNAPSHOT_TTL_SEC``
    seconds (default 60) so Stage A's per-retrieval call doesn't
    burn a UC query each time. Pass ``force_refresh=True`` to bypass
    the cache (used by ``brickvision indexer status`` /
    ``brickvision indexer health`` so an operator running the CLI
    immediately sees post-promotion state without waiting for TTL).
    """

    global _ACTIVE_SNAPSHOT_CACHE

    if not force_refresh and _ACTIVE_SNAPSHOT_CACHE is not None:
        cached_at, cached_value = _ACTIVE_SNAPSHOT_CACHE
        ttl = _ttl_seconds()
        if ttl > 0 and (time.monotonic() - cached_at) < ttl:
            return cached_value

    if _is_dry_run():
        snapshot = _read_dry_run_active_snapshot()
    else:
        snapshot = _query_active_snapshot_via_uc()

    _ACTIVE_SNAPSHOT_CACHE = (time.monotonic(), snapshot)
    return snapshot


def _invalidate_active_snapshot_cache() -> None:
    """Clear the TTL cache. Public hook for the CLI rollback path
    so a successful rollback immediately reflects in the next
    ``status`` / ``health`` call.
    """

    global _ACTIVE_SNAPSHOT_CACHE
    _ACTIVE_SNAPSHOT_CACHE = None


def list_extensions_with_exemplars(
    *,
    user_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return Extension rows that have a hand-authored skill exemplar.

    Source: ``<BV_CATALOG>.<BV_SCHEMA>.extensions WHERE has_exemplar=true``
    joined with ``<BV_CATALOG>.<BV_SCHEMA>.meta_skills`` for the parent
    Meta-Skill name and ``<BV_CATALOG>.<BV_SCHEMA>.top_orders`` for the
    Top-Order classification.

    Returned shape (one dict per row) projects onto
    ``SkillCatalogEntry`` in ``apps/console/src/lib/types.ts`` until
    C.1 BULK lands the dedicated ``CapabilityExtension`` shape:

    .. code-block:: python

        {
            "skill_id": "skill:<id>",                 # the exemplar's id
            "version": "<exemplar version>",
            "description": "<extension description>",
            "when_to_use": "<extension semantic blurb>",
            "kind": "mechanical_dag" | "llm_with_tools",
            "layer": 0,
            "last_eval_pass_rate": <float|None>,
            "last_eval_at": "<iso ts>|None",
        }

    C.1 SHELL stub: returns ``[]`` because the indexer has never run.
    The SPA's ``/catalog`` route renders an honest bootstrap empty
    state explaining where extensions come from. C.1 BULK fills this
    in once ``<BV_CATALOG>.<BV_SCHEMA>.extensions`` exists.
    """

    snapshot = _active_snapshot()
    if snapshot is None:
        return []

    return []


def kg_search_dual_substrate(
    *,
    query: str,
    workspace_claims: Iterable[Mapping[str, Any]] = (),
    workspace_edges: Iterable[tuple[str, str, str]] = (),
    capability_substrate_claims: Iterable[Mapping[str, Any]] = (),
    capability_substrate_edges: Iterable[tuple[str, str, str]] = (),
    k: int = 20,
    defaults: KGSearchDefaults | None = None,
    subject_card_hashes: Mapping[str, str] | None = None,
) -> DualSubstrateResult:
    """Stage A retrieval: walk capability graph AND workspace KG.

    The primary substrate is the capability graph (source-of-truth for
    "what Databricks offers"). The secondary substrate is the partner's
    workspace KG (source-of-truth for "what's in the partner's
    workspace"). When both substrates speak about the same UC entity
    (e.g., a deprecated SDK method that exists in docs but is missing
    from the partner's installed SDK), arbitration is by
    ``source_authority``: capability-graph wins for capability claims,
    workspace KG wins for installed-state claims. Disagreement on the
    SAME claim type surfaces as ``CAPABILITY_WORKSPACE_MISMATCH`` and
    pauses the build for HITL triage.

    C.1 SHELL stub: when ``capability_substrate_claims`` is empty (the
    indexer hasn't run), this function delegates entirely to the legacy
    ``kg_search`` against the workspace KG, exactly preserving the
    pre-v0.7.7 Stage A behaviour. C.1 BULK extends the body to merge
    the two substrate seed sets through the existing PPR pipeline and
    arbitrate via ``source_authority``.

    See ``docs/14-context-engineering.md`` §11.5.1.A and
    ``docs/23-databricks-capability-graph.md`` §23.5 for the full
    contract.
    """

    cap_claims = list(capability_substrate_claims)
    cap_edges = list(capability_substrate_edges)
    ws_claims = list(workspace_claims)
    ws_edges = list(workspace_edges)

    snapshot = _active_snapshot()

    # Workspace substrate is always available (the existing
    # kg_search). When the capability substrate is unavailable (no
    # active snapshot OR no capability claims supplied) Stage A
    # degrades to workspace-only retrieval — exactly the pre-v0.7.7
    # behaviour, pinned bit-for-bit by
    # ``test_dual_substrate_with_empty_capability_degrades_to_kg_search``
    # in ``tests/unit/test_self_bootstrap_capability_graph.py``.
    if snapshot is None or not cap_claims:
        workspace_result = kg_search(
            query=query,
            claims=ws_claims,
            edges=ws_edges,
            k=k,
            defaults=defaults,
            return_diagnostic=True,
            subject_card_hashes=subject_card_hashes,
        )
        assert isinstance(workspace_result, KGSearchResult)
        return DualSubstrateResult(
            capability_refs=(),
            workspace_refs=workspace_result.refs,
            mismatch_subjects=(),
            snapshot_id=None,
            reason_codes=workspace_result.reason_codes,
        )

    # ------------------------------------------------------------------
    # N173 BULK — joint dual-substrate PPR.
    #
    # 1. Lexical retrieval per substrate (so we can attribute each ref
    #    back to its originating substrate AND apply per-substrate
    #    authority weights to the seed score).
    # 2. Authority arbitration: workspace seeds × 1.0; capability seeds
    #    × ``claim["authority_weight"]`` (or _DEFAULT_CAPABILITY_AUTHORITY
    #    when the field is absent — matches the docs default in
    #    ``<BV_CATALOG>.<BV_SCHEMA>.source_authority``).
    # 3. Joint PPR: merge the lexical seeds into ONE personalization
    #    vector via the existing §11.5.1.D contract (re-using
    #    ``_build_personalization`` keeps the seed-merging behaviour
    #    that ``test_kg_search_is_deterministic_across_repeated_runs``
    #    pins). The subgraph is the union of both substrates' edges.
    # 4. Substrate attribution: refs whose subject is **only** in the
    #    capability substrate go to ``capability_refs``; refs whose
    #    subject is **only** in the workspace substrate go to
    #    ``workspace_refs``; subjects appearing in **both** go to the
    #    substrate whose authority-weighted lexical score is higher
    #    (ties → workspace, since workspace facts are factual about the
    #    partner's environment).
    # 5. Mismatch detection: subjects appearing in both substrates with
    #    a shared predicate whose ``value_json`` contradicts surface
    #    ``CAPABILITY_WORKSPACE_MISMATCH`` per
    #    ``docs/14-context-engineering.md`` §11.5.1.A + the build
    #    pipeline reason-codes catalog ``docs/05-build-pipeline.md`` §7.6.
    # ------------------------------------------------------------------

    cfg = defaults or KGSearchDefaults()

    cap_lexical_raw = kg_retrieve(
        query=query,
        claims=cap_claims,
        max_refs=cfg.seed_ceiling,
        subject_card_hashes=subject_card_hashes,
    )
    ws_lexical = kg_retrieve(
        query=query,
        claims=ws_claims,
        max_refs=cfg.seed_ceiling,
        subject_card_hashes=subject_card_hashes,
    )

    # Build per-claim authority lookup so we can re-weight the
    # capability lexical seeds without mutating the source rows.
    cap_authority_by_claim = _capability_authority_index(cap_claims)
    cap_lexical = tuple(
        dataclasses.replace(
            ref,
            score=ref.score
            * cap_authority_by_claim.get(
                ref.claim_id, _DEFAULT_CAPABILITY_AUTHORITY
            ),
        )
        for ref in cap_lexical_raw
    )
    # Workspace seeds are weighted at fixed 1.0; ``ref.score`` is
    # already the lexical match score so this is the identity
    # transform — explicit so the symmetry with the capability path is
    # easy to audit.
    ws_lexical_w = tuple(
        dataclasses.replace(ref, score=ref.score * _WORKSPACE_AUTHORITY)
        for ref in ws_lexical
    )

    merged_lexical: tuple[ClaimRef, ...] = cap_lexical + ws_lexical_w
    merged_edges: tuple[tuple[str, str, str], ...] = tuple(cap_edges) + tuple(
        ws_edges
    )

    structural = tuple(
        _structural_seeds(lexical=list(merged_lexical), edges=list(merged_edges))
    )
    personalization = _build_personalization(
        lexical=merged_lexical, structural=structural, cfg=cfg
    )
    ppr_scores, _iterations = _ppr_scores(
        edges=list(merged_edges), personalization=personalization, cfg=cfg
    )

    # Substrate attribution maps. ``cap_subjects_only`` /
    # ``ws_subjects_only`` are mutually exclusive; ``shared_subjects``
    # is the intersection (mismatch candidates).
    cap_subjects = {c["subject"] for c in cap_claims}
    ws_subjects = {c["subject"] for c in ws_claims}
    shared_subjects = cap_subjects & ws_subjects

    # Per-substrate maximum lexical (already-authority-weighted) score
    # is the tie-breaker for shared subjects — whichever substrate
    # produced the strongest authority-weighted seed for a subject
    # claims that subject's downstream PPR mass.
    cap_max_score: dict[str, float] = {}
    for ref in cap_lexical:
        prev = cap_max_score.get(ref.subject, 0.0)
        if ref.score > prev:
            cap_max_score[ref.subject] = ref.score
    ws_max_score: dict[str, float] = {}
    for ref in ws_lexical_w:
        prev = ws_max_score.get(ref.subject, 0.0)
        if ref.score > prev:
            ws_max_score[ref.subject] = ref.score

    capability_refs = _rank_substrate_refs(
        lexical=cap_lexical,
        structural=structural,
        ppr_scores=ppr_scores,
        cfg=cfg,
        own_subjects=cap_subjects,
        peer_subjects=ws_subjects,
        own_max_score=cap_max_score,
        peer_max_score=ws_max_score,
        attribute_ties_to_self=False,  # workspace wins ties
        k=k,
    )
    workspace_refs = _rank_substrate_refs(
        lexical=ws_lexical_w,
        structural=structural,
        ppr_scores=ppr_scores,
        cfg=cfg,
        own_subjects=ws_subjects,
        peer_subjects=cap_subjects,
        own_max_score=ws_max_score,
        peer_max_score=cap_max_score,
        attribute_ties_to_self=True,
        k=k,
    )

    mismatch_subjects = _detect_mismatches(
        capability_claims=cap_claims,
        workspace_claims=ws_claims,
        shared_subjects=shared_subjects,
    )

    reason_codes: list[str] = []
    if mismatch_subjects:
        reason_codes.append(ReasonCode.CAPABILITY_WORKSPACE_MISMATCH.value)

    return DualSubstrateResult(
        capability_refs=capability_refs,
        workspace_refs=workspace_refs,
        mismatch_subjects=mismatch_subjects,
        snapshot_id=snapshot.snapshot_id,
        reason_codes=tuple(reason_codes),
    )


# ---------------------------------------------------------------------------
# N173 BULK private helpers.
#
# These are intentionally module-private (single-underscore) because they
# bake the §11.5.1.A dual-substrate contract into one place. Re-using
# them outside ``kg_search_dual_substrate`` would duplicate the
# authority-arbitration policy.
# ---------------------------------------------------------------------------


def _capability_authority_index(
    claims: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Map ``claim_id`` → per-row authority weight.

    The indexer's ``persist_to_uc`` task hydrates each capability
    claim row with an ``authority_weight`` field copied from
    ``<BV_CATALOG>.<BV_SCHEMA>.source_provenance`` (per
    ``docs/23-databricks-capability-graph.md`` §23.1). When the field
    is absent (e.g., a synthetic test fixture) this function omits
    the row from the index so the call site falls back to
    ``_DEFAULT_CAPABILITY_AUTHORITY``. Coercion is strict: only float
    / int values are accepted; anything else (None, str, etc.) is
    treated as missing so a malformed row can never silently shift
    arbitration.
    """

    out: dict[str, float] = {}
    for row in claims:
        weight = row.get("authority_weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            out[row["claim_id"]] = float(weight)
    return out


def _rank_substrate_refs(
    *,
    lexical: Sequence[ClaimRef],
    structural: Sequence[ClaimRef],
    ppr_scores: Mapping[str, float],
    cfg: KGSearchDefaults,
    own_subjects: set[str],
    peer_subjects: set[str],
    own_max_score: Mapping[str, float],
    peer_max_score: Mapping[str, float],
    attribute_ties_to_self: bool,
    k: int,
) -> tuple[ClaimRef, ...]:
    """Rank refs that should be attributed to one substrate.

    Mirrors the score-merging policy in ``kg_search`` (lexical refs
    get ``cfg.w_doc * lexical_score + (1 - cfg.w_doc) * ppr_score``;
    structural refs get ``cfg.w_struct * (struct_score + ppr_score) /
    2.0``) but only emits refs whose subject is attributed to **this**
    substrate. Shared-subject attribution is decided by comparing
    ``own_max_score`` vs ``peer_max_score`` for that subject; ties go
    to the substrate where ``attribute_ties_to_self`` is ``True``
    (workspace wins ties since workspace facts are factual about the
    partner's environment).
    """

    def _attributed_to_self(subject: str) -> bool:
        if subject in own_subjects and subject not in peer_subjects:
            return True
        if subject in peer_subjects and subject not in own_subjects:
            return False
        # Shared subject — arbitrate by authority-weighted lexical score.
        own = own_max_score.get(subject, 0.0)
        peer = peer_max_score.get(subject, 0.0)
        if own > peer:
            return True
        if peer > own:
            return False
        return attribute_ties_to_self

    merged: dict[str, ClaimRef] = {}
    for ref in lexical:
        if not _attributed_to_self(ref.subject):
            continue
        ppr_score = ppr_scores.get(ref.subject, 0.0)
        weighted = cfg.w_doc * ref.score + (1.0 - cfg.w_doc) * ppr_score
        merged[ref.claim_id] = dataclasses.replace(ref, score=weighted)
    for ref in structural:
        if ref.claim_id in merged:
            continue
        if not _attributed_to_self(ref.subject):
            continue
        ppr_score = ppr_scores.get(ref.subject, 0.0)
        merged[ref.claim_id] = dataclasses.replace(
            ref, score=cfg.w_struct * (ref.score + ppr_score) / 2.0
        )

    ranked = sorted(merged.values(), key=lambda r: r.score, reverse=True)
    return tuple(ranked[:k])


def _detect_mismatches(
    *,
    capability_claims: Sequence[Mapping[str, Any]],
    workspace_claims: Sequence[Mapping[str, Any]],
    shared_subjects: set[str],
) -> tuple[str, ...]:
    """Return shared subjects with at least one contradicting predicate.

    A "contradiction" is: same ``subject`` + same ``predicate`` +
    different ``value_json`` between the two substrates. This is the
    exact condition that triggers ``CAPABILITY_WORKSPACE_MISMATCH``
    per ``docs/14-context-engineering.md`` §11.5.1.A; HITL review
    decides authority for that build.

    Subjects with no shared predicates (e.g., the capability graph
    asserts ``EXISTS`` and the workspace KG asserts ``OWNED_BY``) are
    NOT a contradiction — they're complementary facts that the joint
    PPR walk happily merges. Only same-predicate disagreement is
    surfaced.
    """

    if not shared_subjects:
        return ()

    cap_by_subject: dict[str, dict[str, Any]] = {}
    for row in capability_claims:
        subject = row["subject"]
        if subject not in shared_subjects:
            continue
        cap_by_subject.setdefault(subject, {})[row["predicate"]] = row["value_json"]

    mismatches: set[str] = set()
    for row in workspace_claims:
        subject = row["subject"]
        if subject not in shared_subjects:
            continue
        cap_predicates = cap_by_subject.get(subject)
        if not cap_predicates:
            continue
        cap_value = cap_predicates.get(row["predicate"])
        if cap_value is None:
            continue
        if cap_value != row["value_json"]:
            mismatches.add(subject)

    return tuple(sorted(mismatches))


__all__ = [
    "CapabilityGraphSnapshot",
    "DualSubstrateResult",
    "kg_search_dual_substrate",
    "list_extensions_with_exemplars",
]
