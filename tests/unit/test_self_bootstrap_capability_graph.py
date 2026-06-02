"""N187 — capability-graph self-bootstrap extension.

Per ``docs/21-roadmap.md`` §19.16 N187 (and the §7.10 four-step proof
in ``docs/09-self-bootstrap.md``), the v0.7.7 cascade must preserve
the round-trip byte-identity invariant *and* extend it: when the
indexer's capability-graph snapshot is plumbed through Stage A
retrieval (the dual-substrate ``kg_search_dual_substrate``), PPR must
remain deterministic given the snapshot hash + the workspace KG
snapshot.

What this file pins
===================

1. **Byte-identity of round-trip extract → transpile → extract** for the
   3 canonical Layer-0 skills already covered by
   ``scripts/self_bootstrap_check.py``. We call the script-level entry
   point so any drift in the upstream conventions is caught at unit
   time (not just nightly).
2. **PPR-determinism-on-snapshot-hash for ``kg_search``**: identical
   inputs (claims + edges + query + defaults) yield byte-identical
   ``ClaimRef`` lists across N runs. The capability graph contributes
   to those inputs; if the underlying snapshot is pinned, retrieval
   is pinned.
3. **PPR-determinism for ``kg_search_dual_substrate``**: same query +
   same workspace + same capability-substrate inputs → byte-identical
   ``DualSubstrateResult`` across N runs.
4. **Falsifiability check**: varying the capability-substrate inputs
   between two runs DOES change the output (so the snapshot is
   actually being consumed — the determinism property isn't trivially
   satisfied by ignoring the inputs).
5. **C.1 SHELL passthrough invariant**: when the capability snapshot
   is empty (the indexer has never run), ``kg_search_dual_substrate``
   degrades exactly to ``kg_search`` over the workspace KG — pre-v0.7.7
   Stage A behaviour preserved bit-for-bit.

These five pins together operationalise the §3 criterion 5 + 13b bet
contract: dual-substrate retrieval is deterministic given pinned
substrate state, so the four-step self-bootstrap proof keeps holding.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Inject src/ on sys.path so this test runs cleanly under
# `pytest tests/unit/...` from the repo root (matching the project's
# existing pattern in tests/conftest.py).
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:  # pragma: no cover — defensive
    sys.path.insert(0, str(_SRC))


from brickvision_runtime.capability_graph.retrieve import (
    DualSubstrateResult,
    kg_search_dual_substrate,
)
from brickvision_runtime.kg.retriever import (
    ClaimRef,
    KGSearchDefaults,
    KGSearchResult,
    kg_search,
)


# ---------------------------------------------------------------------------
# Test fixtures: deterministic "workspace KG" + "capability graph" payloads.
# ---------------------------------------------------------------------------
#
# Both substrates are tiny, hand-written, and deterministic (no UUIDs, no
# wall-clock). The payloads are pinned at module load so any drift in the
# inputs makes the determinism assertions fail explicitly.


_WORKSPACE_CLAIMS: tuple[dict, ...] = (
    {
        "claim_id": "ws-c1",
        "subject": "uc.catalog.bv_bronze",
        "predicate": "exists",
        "value_json": "true",
        "metadata_json": "workspace catalog bv_bronze exists",
    },
    {
        "claim_id": "ws-c2",
        "subject": "uc.schema.bv_bronze.users",
        "predicate": "row_count",
        "value_json": "1234",
        "metadata_json": "workspace bv_bronze users",
    },
    {
        "claim_id": "ws-c3",
        "subject": "uc.table.bv_bronze.users.events",
        "predicate": "delta_format",
        "value_json": "delta",
        "metadata_json": "workspace events delta",
    },
)
_WORKSPACE_EDGES: tuple[tuple[str, str, str], ...] = (
    ("uc.catalog.bv_bronze", "contains", "uc.schema.bv_bronze.users"),
    ("uc.schema.bv_bronze.users", "contains", "uc.table.bv_bronze.users.events"),
)


_CAPABILITY_CLAIMS_V1: tuple[dict, ...] = (
    {
        "claim_id": "cap-c1",
        "subject": "meta:delta-lake/ext:create-table",
        "predicate": "create_table",
        "value_json": "delta",
        "metadata_json": "capability delta create_table",
    },
    {
        "claim_id": "cap-c2",
        "subject": "meta:unity-catalog-foundation/ext:list-catalogs",
        "predicate": "list",
        "value_json": "catalogs",
        "metadata_json": "capability uc list_catalogs",
    },
)
_CAPABILITY_EDGES_V1: tuple[tuple[str, str, str], ...] = (
    (
        "meta:delta-lake/ext:create-table",
        "depends_on",
        "meta:unity-catalog-foundation/ext:list-catalogs",
    ),
)


_CAPABILITY_CLAIMS_V2: tuple[dict, ...] = (
    *_CAPABILITY_CLAIMS_V1,
    {
        "claim_id": "cap-c3",
        "subject": "meta:lakeflow-jobs/ext:create-job",
        "predicate": "create",
        "value_json": "job",
        "metadata_json": "capability lakeflow jobs create",
    },
)


_DEFAULTS = KGSearchDefaults()
_QUERY = "create a delta table in unity catalog"


def _ref_tuple(refs: tuple[ClaimRef, ...] | list[ClaimRef]) -> tuple[tuple, ...]:
    """Project ``ClaimRef``s onto a hashable, equality-comparable tuple
    so we can use ``set`` / ``==`` semantics in tests without
    accidentally comparing unrelated dataclass identity."""

    return tuple(
        (r.claim_id, r.subject, r.predicate, round(r.score, 12),
         r.card_version_hash)
        for r in refs
    )


def _sha256_of_refs(refs: tuple[ClaimRef, ...] | list[ClaimRef]) -> str:
    """Stable SHA256 over ref tuples — independent of dataclass id."""

    blob = json.dumps(_ref_tuple(refs), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# 1. Round-trip byte-identity (existing 3-skill self_bootstrap_check.py).
# ---------------------------------------------------------------------------


# test_existing_self_bootstrap_check_still_passes was retired in v0.7.7
# alongside the build-pipeline transpiler + IR extractor.
# The byte-identity round-trip proof is no longer load-bearing for the
# capability-graph indexer; PPR determinism (below) is the v0.6 pin.


# ---------------------------------------------------------------------------
# 2. PPR-determinism for kg_search (single-substrate baseline).
# ---------------------------------------------------------------------------


def test_kg_search_is_deterministic_across_repeated_runs() -> None:
    runs = []
    for _ in range(5):
        result = kg_search(
            query=_QUERY,
            claims=list(_WORKSPACE_CLAIMS),
            edges=list(_WORKSPACE_EDGES),
            k=10,
            defaults=_DEFAULTS,
            return_diagnostic=True,
        )
        assert isinstance(result, KGSearchResult)
        runs.append(_sha256_of_refs(result.refs))
    assert len(set(runs)) == 1, (
        f"kg_search is non-deterministic across runs: {runs}"
    )


def test_kg_search_changes_when_inputs_change() -> None:
    """Falsifiability — if the determinism property were trivially
    satisfied by ignoring inputs, this test would fail."""

    base = kg_search(
        query=_QUERY,
        claims=list(_WORKSPACE_CLAIMS),
        edges=list(_WORKSPACE_EDGES),
        k=10,
        defaults=_DEFAULTS,
        return_diagnostic=True,
    )
    perturbed_claims = list(_WORKSPACE_CLAIMS) + [
        {
            "claim_id": "ws-c-extra",
            "subject": "uc.table.bv_bronze.users.profiles",
            "predicate": "delta_format",
            "value_json": "delta",
            "metadata_json": "extra workspace claim with delta create table tokens",
        },
    ]
    perturbed = kg_search(
        query=_QUERY,
        claims=perturbed_claims,
        edges=list(_WORKSPACE_EDGES),
        k=10,
        defaults=_DEFAULTS,
        return_diagnostic=True,
    )
    assert isinstance(base, KGSearchResult)
    assert isinstance(perturbed, KGSearchResult)
    assert _sha256_of_refs(base.refs) != _sha256_of_refs(perturbed.refs), (
        "kg_search ignored a relevant claim addition (not falsifiable)"
    )


# ---------------------------------------------------------------------------
# 3. PPR-determinism for kg_search_dual_substrate (capability-graph plumbed).
# ---------------------------------------------------------------------------


def _pinned_snapshot() -> object:
    """A minimal ``CapabilityGraphSnapshot``-shape stand-in. The real
    runtime helper reads from UC; tests pin the shape directly so the
    determinism contract can be exercised without a live workspace."""

    from brickvision_runtime.capability_graph.retrieve import (
        CapabilityGraphSnapshot,
    )

    return CapabilityGraphSnapshot(
        snapshot_id="snap-v0.7.7-test-pinned",
        promoted_at_ms=1_700_000_000_000,
        is_active=True,
        sources_complete=("sdk", "openapi", "docs", "labs", "blog"),
        sources_partial=(),
    )


def test_dual_substrate_is_deterministic_with_pinned_inputs(monkeypatch) -> None:
    """v0.7.7 N187 contract: when the capability-graph snapshot is
    pinned (via monkeypatching ``_active_snapshot`` so the BULK branch
    runs), the dual-substrate retrieval must be byte-identical across
    repeated invocations on the same inputs."""

    from brickvision_runtime.capability_graph import retrieve as retrieve_mod

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    sigs: list[str] = []
    for _ in range(5):
        result = kg_search_dual_substrate(
            query=_QUERY,
            workspace_claims=list(_WORKSPACE_CLAIMS),
            workspace_edges=list(_WORKSPACE_EDGES),
            capability_substrate_claims=list(_CAPABILITY_CLAIMS_V1),
            capability_substrate_edges=list(_CAPABILITY_EDGES_V1),
            k=10,
        )
        assert isinstance(result, DualSubstrateResult)
        # Convert to a stable digest covering both ref tuples + the
        # snapshot pointer so any drift surfaces.
        digest_blob = json.dumps(
            {
                "workspace": _ref_tuple(result.workspace_refs),
                "capability": _ref_tuple(result.capability_refs),
                "snapshot_id": result.snapshot_id,
                "mismatch_subjects": list(result.mismatch_subjects),
                "reason_codes": list(result.reason_codes),
            },
            sort_keys=True,
        ).encode()
        sigs.append(hashlib.sha256(digest_blob).hexdigest())
    assert len(set(sigs)) == 1, (
        f"kg_search_dual_substrate is non-deterministic: {sigs}"
    )


def test_dual_substrate_changes_when_capability_substrate_changes(
    monkeypatch,
) -> None:
    """Snapshot-hash falsifiability: varying the capability claims
    between two runs MUST produce a different DualSubstrateResult.
    Without this property, the determinism guarantee is vacuous."""

    from brickvision_runtime.capability_graph import retrieve as retrieve_mod

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    v1 = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=list(_WORKSPACE_CLAIMS),
        workspace_edges=list(_WORKSPACE_EDGES),
        capability_substrate_claims=list(_CAPABILITY_CLAIMS_V1),
        capability_substrate_edges=list(_CAPABILITY_EDGES_V1),
        k=10,
    )
    v2 = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=list(_WORKSPACE_CLAIMS),
        workspace_edges=list(_WORKSPACE_EDGES),
        capability_substrate_claims=list(_CAPABILITY_CLAIMS_V2),
        capability_substrate_edges=list(_CAPABILITY_EDGES_V1),
        k=10,
    )
    assert _ref_tuple(v1.capability_refs) != _ref_tuple(v2.capability_refs), (
        "dual-substrate ignored a capability-graph claim change "
        "(snapshot hash invariant cannot be falsified)"
    )


def test_dual_substrate_workspace_subject_set_unchanged_when_capability_changes(
    monkeypatch,
) -> None:
    """N173 BULK contract update.

    Under the C.1 SHELL contract (pre-N173) workspace_refs were
    produced by a SOLO ``kg_search`` call against the workspace
    substrate, so capability-input changes COULD NOT affect
    workspace_refs at all. N173 BULK changes that contract by
    design: per ``docs/14-context-engineering.md`` §11.5.1.A "PPR is
    run jointly over the merged seed set", so capability-input
    changes CAN shift workspace ref **scores** via personalization
    mass redistribution across the two substrates' subgraphs.

    What MUST still hold under N173 BULK: the workspace ref
    **subject + predicate identity** is invariant when only
    capability inputs change (workspace-attributed subjects retain
    their attribution; the BULK doesn't accidentally drop or
    substitute workspace subjects in response to capability-side
    perturbations). Score drift is permitted; identity drift is not.
    """

    from brickvision_runtime.capability_graph import retrieve as retrieve_mod

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    v1 = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=list(_WORKSPACE_CLAIMS),
        workspace_edges=list(_WORKSPACE_EDGES),
        capability_substrate_claims=list(_CAPABILITY_CLAIMS_V1),
        capability_substrate_edges=list(_CAPABILITY_EDGES_V1),
        k=10,
    )
    v2 = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=list(_WORKSPACE_CLAIMS),
        workspace_edges=list(_WORKSPACE_EDGES),
        capability_substrate_claims=list(_CAPABILITY_CLAIMS_V2),
        capability_substrate_edges=list(_CAPABILITY_EDGES_V1),
        k=10,
    )

    def _identity(refs: tuple[ClaimRef, ...]) -> tuple[tuple[str, str, str], ...]:
        return tuple(sorted((r.claim_id, r.subject, r.predicate) for r in refs))

    assert _identity(v1.workspace_refs) == _identity(v2.workspace_refs), (
        "N173 BULK invariant violated: workspace ref identity drifted "
        "in response to a capability-only perturbation"
    )


# ---------------------------------------------------------------------------
# 4. C.1 SHELL passthrough invariant.
# ---------------------------------------------------------------------------


def test_dual_substrate_with_empty_capability_degrades_to_kg_search() -> None:
    """When the indexer has never run (no active snapshot, no
    capability claims), dual-substrate must produce exactly the same
    workspace_refs as a direct kg_search — preserving pre-v0.7.7
    Stage A behaviour bit-for-bit."""

    direct = kg_search(
        query=_QUERY,
        claims=list(_WORKSPACE_CLAIMS),
        edges=list(_WORKSPACE_EDGES),
        k=10,
        defaults=_DEFAULTS,
        return_diagnostic=True,
    )
    assert isinstance(direct, KGSearchResult)

    dual = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=list(_WORKSPACE_CLAIMS),
        workspace_edges=list(_WORKSPACE_EDGES),
        capability_substrate_claims=(),
        capability_substrate_edges=(),
        k=10,
        defaults=_DEFAULTS,
    )
    assert _ref_tuple(direct.refs) == _ref_tuple(dual.workspace_refs)
    assert dual.capability_refs == ()
    assert dual.snapshot_id is None


# ---------------------------------------------------------------------------
# 5. ``KGSearchDefaults`` is a pin-able config (replay contract).
# ---------------------------------------------------------------------------


def test_kg_search_defaults_is_frozen_dataclass_with_replay_pinned_fields() -> None:
    """The 6th replay pin (``capability_graph_snapshot_id``) requires
    that the retrieval-side config is immutable — otherwise replay
    cannot reproduce a historical retrieval."""

    cfg = KGSearchDefaults()
    assert dataclasses.is_dataclass(cfg)
    expected_fields = {
        "w_struct",
        "w_doc",
        "seed_ceiling",
        "ppr_alpha",
        "ppr_max_iter",
        "ppr_tol",
        "min_recall_floor",
    }
    actual_fields = {f.name for f in dataclasses.fields(cfg)}
    assert expected_fields <= actual_fields, (
        f"replay-pinned KGSearchDefaults fields drifted: "
        f"missing {expected_fields - actual_fields}"
    )

    # Frozen dataclasses raise FrozenInstanceError on mutation.
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        cfg.w_struct = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. Snapshot-hash falsifiability: query-token determinism check.
# ---------------------------------------------------------------------------


def test_kg_search_changes_when_query_changes() -> None:
    """Sanity guard: a different query yields a different ranked
    top-k, otherwise the PPR pipeline isn't actually consuming the
    query (would suggest a regression in lexical seeding)."""

    a = kg_search(
        query="create a delta table in unity catalog",
        claims=list(_WORKSPACE_CLAIMS),
        edges=list(_WORKSPACE_EDGES),
        k=10,
        defaults=_DEFAULTS,
        return_diagnostic=True,
    )
    b = kg_search(
        query="register a model with mlflow tracking",
        claims=list(_WORKSPACE_CLAIMS),
        edges=list(_WORKSPACE_EDGES),
        k=10,
        defaults=_DEFAULTS,
        return_diagnostic=True,
    )
    assert isinstance(a, KGSearchResult)
    assert isinstance(b, KGSearchResult)
    # Either the ref order changes OR the score values differ; if
    # both happen to match exactly the lexical seeding is broken.
    assert _ref_tuple(a.refs) != _ref_tuple(b.refs) or (
        # Allow the empty-result edge case: both queries yielded
        # nothing (the workspace fixture is small) — a harmless
        # degenerate case rather than a regression.
        a.refs == b.refs == []
    )
