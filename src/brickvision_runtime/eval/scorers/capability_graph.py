"""v0.7.7 Capability Graph scorers (11 checks).

Per ``docs/23-databricks-capability-graph.md`` §23.5 the eval framework
adds 11 mechanically-checkable scorers that verify the **runtime**
state of the Capability Graph satisfies its contract every time a
new snapshot is built. They are the read-side counterpart of the 4
install pre-flights (``brickvision.install.preflight.capability_graph``):
the pre-flights run *once* at deploy and block install on miss; the
scorers run *continuously* (nightly + on every smoke gate) and surface
drift after install.

Scorer index
============

================================================  ========================================================  ===========================
Scorer                                            Reason code                                               Gold set / pure assertion
================================================  ========================================================  ===========================
``capability_graph_schema_integrity``             ``CAPABILITY_GRAPH_PERSIST_FAILED``                       pure (13 DDL strings)
``capability_graph_smoke_test_pass_rate``         ``CAPABILITY_GRAPH_SMOKE_REGRESSION``                     ``capability_graph_skill_catalog_gold``
``indexer_dag_task_spec``                         ``CAPABILITY_GRAPH_PROMOTION_FAILED``                     ``indexer_dag_topology_gold``
``budget_namespace_isolation``                    ``INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED``                 pure (delegates to install pre-flight)
``service_principal_isolation``                   ``INDEXER_SP_NOT_PROVISIONED``                            pure (delegates to install pre-flight)
``vector_search_endpoint_grants``                 ``VS_ENDPOINT_GRANTS_MIXED``                              pure (delegates to install pre-flight)
``source_authority_assignment``                   ``CAPABILITY_GRAPH_PERSIST_FAILED``                       pure (locked authority weights)
``hand_authored_skill_exemplar_linkage``          ``HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF``               ``hand_authored_skill_exemplar_links_gold``
``hand_authored_skill_anchor_grounding``          ``HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED``        live source-provenance projection
``indexer_refresh_slo``                           ``CAPABILITY_GRAPH_SNAPSHOT_STALE``                       pure (freshness_tolerance_days)
``knowledge_ui_vocabulary_coverage``              ``DOCS_SECTION_ALIAS_MISSING``                            ``capability_graph_taxonomy_gold``
================================================  ========================================================  ===========================

House-style invariants
======================

- Each scorer is **pure** (no live SDK calls). The caller — a
  scheduled Job, the post-promote validator, or a unit test — is
  responsible for fetching live state via Spark / SDK and passing
  it in via typed kwargs.
- Each scorer returns ``ScorerResult(score, reason_codes, details)``
  where ``score`` is in ``[0.0, 1.0]`` (0 = full miss, 1 = full pass,
  partial scores allowed for fan-out checks).
- ``reason_codes`` is **always non-empty when score < 1.0** and is
  always drawn from the v0.7.7 catalog in :mod:`brickvision_runtime.failures`.
- ``details`` payload is bounded (top-32 violations) so the audit
  row stays under the 64 KiB Delta cell budget.

Three of the scorers (``budget_namespace_isolation``,
``service_principal_isolation``, ``vector_search_endpoint_grants``)
**delegate to the install pre-flight check functions** in
:mod:`brickvision.install.preflight.capability_graph`. This avoids
duplicating logic and guarantees the install gate and the runtime
scorer enforce the same contract bit-for-bit.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from typing import Any

from brickvision_runtime.eval.gold.capability_graph import (
    SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD,
    SEED_HAND_AUTHORED_EXEMPLAR_LINK_GOLD,
    SEED_INDEXER_DAG_TOPOLOGY_GOLD,
    SEED_META_SKILL_GOLD,
    SEED_TOP_ORDER_GOLD,
    HandAuthoredExemplarLinkGoldRow,
    IndexerDAGTaskGoldRow,
)
from brickvision_runtime.eval.scorers import ScorerResult, register_scorer
from brickvision_runtime.failures import ReasonCode


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate(seq: Sequence[Any], cap: int = 32) -> list[Any]:
    """Bounded list materialisation — keeps ``details`` under the
    64 KiB Delta cell budget."""

    out = list(seq)
    return out[:cap]


def _passing_result(details: dict[str, Any] | None = None) -> ScorerResult:
    return ScorerResult(score=1.0, reason_codes=(), details=details or {})


# ---------------------------------------------------------------------------
# 1. CapabilityGraphSchemaIntegrity
#
# Verifies the 13 Delta DDL strings render cleanly with the partner
# catalog substituted, and that each carries the 4 §23.4.2 invariant
# columns where applicable (snapshot_id, last_indexed_at_ms,
# content_hash, source_kind — table-dependent).
# ---------------------------------------------------------------------------


# Per §23.4.2: tables keyed by snapshot_id must have a ``snapshot_id``
# column as their first non-comment column. Closed-set tables use
# ``schema_version`` instead. Both are enforced here.
_SNAPSHOT_KEYED_TABLES: frozenset[str] = frozenset(
    {
        "corpus_snapshots",
        "top_orders",
        "meta_skills",
        "extensions",
        "entity_edges",
        "source_provenance",
    }
)
_SCHEMA_VERSIONED_TABLES: frozenset[str] = frozenset(
    {
        "source_authority",
        "docs_section_aliases",
    }
)
_EXPECTED_TABLE_COUNT: int = 13


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="CapabilityGraphSchemaIntegrity",
)
def capability_graph_schema_integrity(
    *,
    all_ddl: Mapping[str, str],
    catalog: str = "brickvision_dev",
    schema: str = "brickvision",
) -> ScorerResult:
    """Asserts the 13 Delta DDL strings render cleanly + carry the
    invariant first-column ``snapshot_id`` / ``schema_version``
    discipline per §23.4.2.

    The caller passes ``all_ddl`` (typically
    :data:`brickvision_runtime.capability_graph.schemas.ALL_DDL`) and the
    target ``catalog`` + ``schema``. The scorer (a) verifies all 13
    tables are present, (b) verifies no unsubstituted ``${BV_CATALOG}``
    or ``${BV_SCHEMA}`` token remains after rendering (per v0.7.7
    schema consolidation every table lives at
    ``<catalog>.<schema>.<table>`` in a single flat schema), and
    (c) verifies each table declares its first column correctly.
    """

    violations: list[dict[str, Any]] = []

    if len(all_ddl) != _EXPECTED_TABLE_COUNT:
        violations.append(
            {
                "kind": "table_count_drift",
                "observed": len(all_ddl),
                "expected": _EXPECTED_TABLE_COUNT,
                "tables": _truncate(sorted(all_ddl.keys())),
            }
        )

    for table_name, ddl in all_ddl.items():
        rendered = ddl.replace("${BV_CATALOG}", catalog).replace(
            "${BV_SCHEMA}", schema
        )
        if "${BV_CATALOG}" in rendered or "${BV_SCHEMA}" in rendered:
            violations.append(
                {
                    "kind": "unsubstituted_placeholder",
                    "table": table_name,
                }
            )
            continue
        first_col = _first_column_name(rendered)
        if table_name in _SNAPSHOT_KEYED_TABLES and first_col != "snapshot_id":
            violations.append(
                {
                    "kind": "snapshot_id_first_column_violation",
                    "table": table_name,
                    "observed_first_col": first_col,
                }
            )
        elif table_name in _SCHEMA_VERSIONED_TABLES and first_col != "schema_version":
            violations.append(
                {
                    "kind": "schema_version_first_column_violation",
                    "table": table_name,
                    "observed_first_col": first_col,
                }
            )

    if violations:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED.value,),
            details={"violations": _truncate(violations)},
        )
    return _passing_result(details={"tables_checked": len(all_ddl)})


_FIRST_COL_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+\S+\s*\(\s*(?:--[^\n]*\n\s*)*([A-Za-z_][A-Za-z_0-9]*)",
    re.IGNORECASE,
)


def _first_column_name(rendered_ddl: str) -> str | None:
    """Extract the first column name from a CREATE TABLE statement.

    Tolerates leading SQL comments inside the column list. Returns
    ``None`` if the DDL doesn't parse — the schema-integrity scorer
    treats that as a structural violation already covered by the
    ``unsubstituted_placeholder`` branch.
    """

    m = _FIRST_COL_RE.search(rendered_ddl)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 2. CapabilityGraphSmokeTestPassRate
#
# Verifies the just-built snapshot's smoke hit-rate is at or above
# the locked baseline. Pairs with capability_graph_skill_catalog_gold
# and the live <BV_CATALOG>.<BV_SCHEMA>.smoke_baseline rows.
# ---------------------------------------------------------------------------


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="CapabilityGraphSmokeTestPassRate",
)
def capability_graph_smoke_test_pass_rate(
    *,
    smoke_baseline: Sequence[Mapping[str, Any]],
    smoke_observed: Mapping[str, Any],
) -> ScorerResult:
    """Asserts ``smoke_observed.observed_hit_rate >= floor`` where
    ``floor`` is the minimum ``baseline_hit_rate`` across the locked
    smoke_baseline rows (the §23.3.3 "lowest baseline is the floor"
    contract).

    Inputs:
      * ``smoke_baseline`` — rows from
        ``<BV_CATALOG>.<BV_SCHEMA>.smoke_baseline`` (or
        :data:`SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD` for offline
        tests).
      * ``smoke_observed`` — the
        ``brickvision_runtime.capability_graph.smoke.SmokeResult``
        marshalled to a Mapping (caller responsibility), with at least
        ``observed_hit_rate`` and ``passed`` fields.

    The scorer also asserts the gold-set query_id set is a subset of
    the live baseline's query_id set: a missing gold query is a
    structural regression (someone removed a baseline row).
    """

    live_query_ids = {str(r["query_id"]) for r in smoke_baseline if "query_id" in r}
    gold_query_ids = {row.query_id for row in SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD}
    missing_from_live = sorted(gold_query_ids - live_query_ids)

    if missing_from_live:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value,),
            details={
                "kind": "gold_query_missing_from_live_baseline",
                "missing": _truncate(missing_from_live),
            },
        )

    floor_candidates = [
        float(r["baseline_hit_rate"])
        for r in smoke_baseline
        if "baseline_hit_rate" in r
    ]
    if not floor_candidates:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value,),
            details={"kind": "empty_baseline"},
        )
    floor = min(floor_candidates)

    observed = float(smoke_observed.get("observed_hit_rate", 0.0))
    if observed < floor:
        return ScorerResult(
            score=observed,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value,),
            details={
                "kind": "hit_rate_below_floor",
                "observed_hit_rate": observed,
                "floor_hit_rate": floor,
                "baseline_size": len(floor_candidates),
            },
        )

    return _passing_result(
        details={
            "observed_hit_rate": observed,
            "floor_hit_rate": floor,
            "baseline_size": len(floor_candidates),
        }
    )


# ---------------------------------------------------------------------------
# 3. IndexerDAGTaskSpec
#
# Asserts databricks.yml's capability_indexer Job declares exactly
# the 13-task DAG topology from §23.3.1. Pairs with
# indexer_dag_topology_gold.
# ---------------------------------------------------------------------------


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="IndexerDAGTaskSpec",
)
def indexer_dag_task_spec(
    *,
    observed_tasks: Sequence[Mapping[str, Any]],
    gold: Sequence[IndexerDAGTaskGoldRow] = SEED_INDEXER_DAG_TOPOLOGY_GOLD,
) -> ScorerResult:
    """Asserts the live DAB-declared DAG matches the 13-task gold.

    Input:
      * ``observed_tasks`` — sequence of ``{"task_key": str,
        "depends_on": Sequence[str]}`` mappings extracted from
        ``databricks.yml`` resources.jobs.capability_indexer.tasks.
        (The caller is responsible for parsing the YAML and
        flattening the ``- task_key: x`` blocks into a flat list.)

    Pass criteria:
      1. Same task_key set as the gold (no extras, no missing).
      2. Each task's ``depends_on`` set equals the gold's set.

    Why set-equality (vs. ordered): YAML doesn't guarantee list
    order in ``depends_on`` and Databricks Jobs doesn't either. The
    DAG semantics are set-based.
    """

    observed_keys = {str(t.get("task_key", "")) for t in observed_tasks}
    gold_keys = {row.task_key for row in gold}

    missing = sorted(gold_keys - observed_keys)
    extra = sorted(observed_keys - gold_keys)
    violations: list[dict[str, Any]] = []

    if missing:
        violations.append({"kind": "missing_tasks", "task_keys": missing})
    if extra:
        violations.append({"kind": "extra_tasks", "task_keys": extra})

    observed_by_key: dict[str, set[str]] = {}
    for t in observed_tasks:
        key = str(t.get("task_key", ""))
        if not key:
            continue
        deps_raw = t.get("depends_on", ()) or ()
        deps_set: set[str] = set()
        for dep in deps_raw:
            if isinstance(dep, Mapping):
                # Block-style: { task_key: x }
                if "task_key" in dep:
                    deps_set.add(str(dep["task_key"]))
            elif isinstance(dep, str):
                deps_set.add(dep)
        observed_by_key[key] = deps_set

    for row in gold:
        if row.task_key not in observed_by_key:
            continue
        observed_deps = observed_by_key[row.task_key]
        gold_deps = set(row.depends_on)
        if observed_deps != gold_deps:
            violations.append(
                {
                    "kind": "depends_on_drift",
                    "task_key": row.task_key,
                    "observed": sorted(observed_deps),
                    "expected": sorted(gold_deps),
                }
            )

    if violations:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_PROMOTION_FAILED.value,),
            details={"violations": _truncate(violations)},
        )
    return _passing_result(
        details={
            "task_count": len(observed_keys),
            "gold_task_count": len(gold_keys),
        }
    )


# ---------------------------------------------------------------------------
# 4. BudgetNamespaceIsolation
#
# Wraps the install pre-flight check_budget_namespace_isolated and
# translates the PreFlightFailure list into a ScorerResult so the
# eval framework surfaces it as a Finding.
# ---------------------------------------------------------------------------


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="BudgetNamespaceIsolation",
)
def budget_namespace_isolation(
    *,
    spec: Any,
    probe: Any,
) -> ScorerResult:
    """Wraps :func:`brickvision.install.preflight.capability_graph
    .check_budget_namespace_isolated` and translates the result into
    a ScorerResult.

    The pre-flight runs at install; this scorer re-runs nightly to
    detect drift (e.g., a partner operator accidentally creating a
    third namespace that overlaps the indexer's ledger).
    """

    from brickvision.install.preflight.capability_graph import (
        check_budget_namespace_isolated,
    )

    failures = check_budget_namespace_isolated(spec=spec, probe=probe)
    if failures:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED.value,),
            details={
                "violations": _truncate(
                    [
                        {
                            "reason_code": f.reason_code.value,
                            "detail": f.detail,
                            "suggested_next_action": f.suggested_next_action,
                        }
                        for f in failures
                    ]
                )
            },
        )
    return _passing_result()


# ---------------------------------------------------------------------------
# 5. ServicePrincipalIsolation
#
# Wraps check_indexer_sp_provisioned. Asserts bv_indexer_sp ≠
# bv_app_sp at runtime (e.g., a partner consolidating SPs after
# install would breach this).
# ---------------------------------------------------------------------------


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="ServicePrincipalIsolation",
)
def service_principal_isolation(
    *,
    spec: Any,
    probe: Any,
) -> ScorerResult:
    """Wraps :func:`brickvision.install.preflight.capability_graph
    .check_indexer_sp_provisioned`."""

    from brickvision.install.preflight.capability_graph import (
        check_indexer_sp_provisioned,
    )

    failures = check_indexer_sp_provisioned(spec=spec, probe=probe)
    if failures:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.INDEXER_SP_NOT_PROVISIONED.value,),
            details={
                "violations": _truncate(
                    [
                        {
                            "reason_code": f.reason_code.value,
                            "detail": f.detail,
                            "suggested_next_action": f.suggested_next_action,
                        }
                        for f in failures
                    ]
                )
            },
        )
    return _passing_result()


# ---------------------------------------------------------------------------
# 6. VectorSearchEndpointGrants
#
# Wraps check_vector_search_endpoint_grants. Asserts the indexer SP
# has WRITE and the app SP has READ-only on each capability-graph
# index.
# ---------------------------------------------------------------------------


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="VectorSearchEndpointGrants",
)
def vector_search_endpoint_grants(
    *,
    spec: Any,
    probe: Any,
) -> ScorerResult:
    """Wraps :func:`brickvision.install.preflight.capability_graph
    .check_vector_search_endpoint_grants`."""

    from brickvision.install.preflight.capability_graph import (
        check_vector_search_endpoint_grants,
    )

    failures = check_vector_search_endpoint_grants(spec=spec, probe=probe)
    if failures:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.VS_ENDPOINT_GRANTS_MIXED.value,),
            details={
                "violations": _truncate(
                    [
                        {
                            "reason_code": f.reason_code.value,
                            "detail": f.detail,
                            "suggested_next_action": f.suggested_next_action,
                        }
                        for f in failures
                    ]
                )
            },
        )
    return _passing_result()


# ---------------------------------------------------------------------------
# 7. SourceAuthorityAssignment
#
# Asserts every SourceProvenanceRow in the live snapshot carries the
# locked authority weight for its source_kind. Drift here means
# either (a) the indexer wrote provenance with a wrong weight, or
# (b) someone tampered with the source_authority closed-set table.
# ---------------------------------------------------------------------------


# Locked at v0.7.7 ship; mirrors graph_builder._SOURCE_AUTHORITY.
_LOCKED_AUTHORITY_WEIGHTS: Mapping[str, float] = {
    "sdk": 1.00,
    "openapi": 0.95,
    "docs": 0.85,
    "labs": 0.75,
    "blog": 0.50,  # base weight; recency-decayed at edge-emission time
    "hand_authored": 0.00,
}

# Blog rows can carry any weight in [0, 0.50] thanks to the
# recency-decayed authority logic. The scorer treats blog rows as
# pass if the weight is within [0, 0.50] inclusive of 0.0001 epsilon.
_BLOG_WEIGHT_MAX: float = 0.50
_BLOG_WEIGHT_MIN: float = 0.0
_WEIGHT_EPSILON: float = 1e-4


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="SourceAuthorityAssignment",
)
def source_authority_assignment(
    *,
    source_authority_rows: Sequence[Mapping[str, Any]],
    provenance_rows: Sequence[Mapping[str, Any]] = (),
) -> ScorerResult:
    """Asserts the locked authority weights are correctly written to
    ``<BV_CATALOG>.<BV_SCHEMA>.source_authority``, and (optionally) that
    each provenance row's source_kind is in the closed set.

    Inputs:
      * ``source_authority_rows`` — every row from the closed-set
        ``<BV_CATALOG>.<BV_SCHEMA>.source_authority`` table; verified
        against ``_LOCKED_AUTHORITY_WEIGHTS``.
      * ``provenance_rows`` — optional sample (or full snapshot) of
        ``<BV_CATALOG>.<BV_SCHEMA>.source_provenance``; verified each
        ``source_kind`` is in the closed set.
    """

    violations: list[dict[str, Any]] = []
    observed_kinds: dict[str, float] = {}

    for row in source_authority_rows:
        kind = str(row.get("source_kind", ""))
        weight = float(row.get("authority_weight", -1.0))
        observed_kinds[kind] = weight

        if kind == "blog":
            if not (
                _BLOG_WEIGHT_MIN - _WEIGHT_EPSILON
                <= weight
                <= _BLOG_WEIGHT_MAX + _WEIGHT_EPSILON
            ):
                violations.append(
                    {
                        "kind": "blog_weight_out_of_band",
                        "source_kind": kind,
                        "observed_weight": weight,
                        "allowed_band": [_BLOG_WEIGHT_MIN, _BLOG_WEIGHT_MAX],
                    }
                )
            continue

        expected = _LOCKED_AUTHORITY_WEIGHTS.get(kind)
        if expected is None:
            violations.append(
                {
                    "kind": "unknown_source_kind",
                    "source_kind": kind,
                    "allowed_kinds": sorted(_LOCKED_AUTHORITY_WEIGHTS.keys()),
                }
            )
            continue
        if abs(weight - expected) > _WEIGHT_EPSILON:
            violations.append(
                {
                    "kind": "weight_drift",
                    "source_kind": kind,
                    "observed_weight": weight,
                    "expected_weight": expected,
                }
            )

    missing_kinds = sorted(
        set(_LOCKED_AUTHORITY_WEIGHTS.keys()) - set(observed_kinds.keys())
    )
    if missing_kinds:
        violations.append(
            {
                "kind": "missing_source_kinds",
                "missing": missing_kinds,
            }
        )

    valid_kinds = set(_LOCKED_AUTHORITY_WEIGHTS.keys())
    for row in provenance_rows:
        kind = str(row.get("source_kind", ""))
        if kind and kind not in valid_kinds:
            violations.append(
                {
                    "kind": "provenance_unknown_source_kind",
                    "source_kind": kind,
                    "entity_id": str(row.get("entity_id", "")),
                }
            )

    if violations:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED.value,),
            details={
                "violations": _truncate(violations),
                "kinds_checked": sorted(observed_kinds.keys()),
            },
        )
    return _passing_result(details={"kinds_checked": sorted(observed_kinds.keys())})


# ---------------------------------------------------------------------------
# 8. HandAuthoredSkillExemplarLinkage
#
# Asserts every ``SKILL.yaml`` in skills/ declares a well-formed
# ``exemplar_of`` and the live capability_graph has a matching
# extension row (or a stub if the indexer hasn't promoted one).
# Pairs with hand_authored_skill_exemplar_links_gold.
# ---------------------------------------------------------------------------


_EXEMPLAR_PTR_RE = re.compile(
    r"^meta:[a-z0-9][a-z0-9_-]*[a-z0-9]/ext:[a-z0-9][a-z0-9_-]*[a-z0-9]$"
)


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="HandAuthoredSkillExemplarLinkage",
)
def hand_authored_skill_exemplar_linkage(
    *,
    observed_skill_exemplars: Mapping[str, str],
    extension_ids: Sequence[str] = (),
    gold: Sequence[HandAuthoredExemplarLinkGoldRow] = SEED_HAND_AUTHORED_EXEMPLAR_LINK_GOLD,
) -> ScorerResult:
    """Asserts every gold hand-authored skill has a SKILL.yaml row
    declaring the right ``exemplar_of`` pointer.

    Inputs:
      * ``observed_skill_exemplars`` — ``skill_id -> exemplar_of``
        mapping derived by walking ``skills/*/SKILL.yaml`` (caller
        owns the YAML I/O).
      * ``extension_ids`` — optional snapshot of all
        ``ExtensionRow.extension_id`` values from the live
        capability_graph.extensions table; if non-empty, the scorer
        also verifies each gold exemplar resolves to a live extension
        (per §23.2.6 the indexer mints a stub when missing, so a
        missing extension is allowed only if the indexer has never run
        — in steady state the live table covers all gold pointers).
    """

    violations: list[dict[str, Any]] = []
    extension_id_set = set(extension_ids)

    for gold_row in gold:
        observed_ptr = observed_skill_exemplars.get(gold_row.skill_id)
        if observed_ptr is None:
            violations.append(
                {
                    "kind": "missing_exemplar_of",
                    "skill_id": gold_row.skill_id,
                    "expected": gold_row.exemplar_of,
                }
            )
            continue
        if observed_ptr != gold_row.exemplar_of:
            violations.append(
                {
                    "kind": "exemplar_drift",
                    "skill_id": gold_row.skill_id,
                    "observed": observed_ptr,
                    "expected": gold_row.exemplar_of,
                }
            )
            continue
        if not _EXEMPLAR_PTR_RE.match(observed_ptr):
            violations.append(
                {
                    "kind": "malformed_exemplar_of",
                    "skill_id": gold_row.skill_id,
                    "observed": observed_ptr,
                }
            )
            continue
        if extension_id_set and observed_ptr not in extension_id_set:
            violations.append(
                {
                    "kind": "exemplar_not_in_capability_graph",
                    "skill_id": gold_row.skill_id,
                    "exemplar_of": observed_ptr,
                }
            )

    extra_skills = sorted(set(observed_skill_exemplars.keys()) - {g.skill_id for g in gold})
    if extra_skills:
        for sid in extra_skills:
            ptr = observed_skill_exemplars[sid]
            kind = (
                "malformed_exemplar_of_extra_skill"
                if not _EXEMPLAR_PTR_RE.match(ptr)
                else "unexpected_skill_not_in_gold"
            )
            violations.append(
                {
                    "kind": kind,
                    "skill_id": sid,
                    "observed": ptr,
                }
            )

    if violations:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF.value,),
            details={
                "violations": _truncate(violations),
                "gold_size": len(gold),
                "observed_size": len(observed_skill_exemplars),
            },
        )
    return _passing_result(
        details={
            "gold_size": len(gold),
            "observed_size": len(observed_skill_exemplars),
        }
    )


# ---------------------------------------------------------------------------
# 9. HandAuthoredSkillAnchorGrounding
#
# Classifies each exemplar anchor by source_provenance. This stays
# separate from HandAuthoredSkillExemplarLinkage: linkage answers
# "does the pointer resolve?", grounding answers "is the resolved
# extension backed by indexed source evidence rather than only by the
# hand-authored skill that pointed at it?"
# ---------------------------------------------------------------------------


_SOURCE_GROUNDING_KINDS: frozenset[str] = frozenset(
    {"sdk", "openapi", "docs", "labs", "blog"}
)


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="HandAuthoredSkillAnchorGrounding",
)
def hand_authored_skill_anchor_grounding(
    *,
    observed_skill_exemplars: Mapping[str, str],
    extension_source_kinds: Mapping[str, Sequence[str]],
    pending_stub_anchors: Sequence[str] = (),
    gold: Sequence[HandAuthoredExemplarLinkGoldRow] = SEED_HAND_AUTHORED_EXEMPLAR_LINK_GOLD,
) -> ScorerResult:
    """Verify hand-authored skill anchors are backed by indexed sources.

    ``extension_source_kinds`` is a caller-provided projection of
    ``source_provenance`` grouped by ``entity_id``. A skill anchor is
    source-grounded when its extension has at least one non-hand-authored
    contributor (SDK, OpenAPI, docs, labs, or blog). Anchors deliberately
    awaiting source coverage must be passed in ``pending_stub_anchors``;
    otherwise a hand-authored-only extension is a validation failure.

    No skill names or extension IDs are special-cased here. The scorer is
    provenance-driven so new partner/core skills follow the same rule
    without adding code.
    """

    violations: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    pending = set(pending_stub_anchors)

    for gold_row in gold:
        anchor = observed_skill_exemplars.get(gold_row.skill_id)
        if anchor is None:
            continue
        source_kinds = tuple(
            sorted({str(kind) for kind in extension_source_kinds.get(anchor, ())})
        )
        source_grounded = any(kind in _SOURCE_GROUNDING_KINDS for kind in source_kinds)
        if source_grounded:
            status = "source_grounded"
        elif anchor in pending:
            status = "pending_stub_explicit"
        elif source_kinds:
            status = "hand_authored_stub_only"
        else:
            status = "missing_source_provenance"

        classification = {
            "skill_id": gold_row.skill_id,
            "exemplar_of": anchor,
            "source_kinds": list(source_kinds),
            "grounding_status": status,
        }
        classifications.append(classification)
        if status in {"hand_authored_stub_only", "missing_source_provenance"}:
            violations.append(classification)

    if violations:
        return ScorerResult(
            score=0.0,
            reason_codes=(
                ReasonCode.HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED.value,
            ),
            details={
                "violations": _truncate(violations),
                "classifications": _truncate(classifications),
                "checked": len(classifications),
            },
        )
    return _passing_result(
        details={
            "classifications": _truncate(classifications),
            "checked": len(classifications),
        }
    )


# ---------------------------------------------------------------------------
# 10. IndexerRefreshSLO
#
# Asserts the active snapshot was promoted within
# freshness_tolerance_days. Surfaces CAPABILITY_GRAPH_SNAPSHOT_STALE.
# ---------------------------------------------------------------------------


_DEFAULT_FRESHNESS_TOLERANCE_DAYS: int = 2  # docs/23 §23.6.1 default
_MS_PER_DAY: int = 24 * 60 * 60 * 1000


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="IndexerRefreshSLO",
)
def indexer_refresh_slo(
    *,
    active_snapshot_promoted_at_ms: int | None,
    now_ms: int,
    freshness_tolerance_days: int = _DEFAULT_FRESHNESS_TOLERANCE_DAYS,
) -> ScorerResult:
    """Asserts the active snapshot was promoted within
    ``freshness_tolerance_days``.

    Inputs:
      * ``active_snapshot_promoted_at_ms`` — value of
        ``<BV_CATALOG>.<BV_SCHEMA>.active_snapshot_id.promoted_at_ms``
        (None ⇒ indexer has never run).
      * ``now_ms`` — caller-supplied wall-clock time (replay-pinned
        per the v0.7.7 6th replay pin contract).
      * ``freshness_tolerance_days`` — defaults to 2 days per §23.6.1
        but partner-tunable via ``BV_INDEXER_FRESHNESS_TOLERANCE_DAYS``.
    """

    if freshness_tolerance_days <= 0:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value,),
            details={
                "kind": "invalid_tolerance",
                "freshness_tolerance_days": freshness_tolerance_days,
            },
        )

    if active_snapshot_promoted_at_ms is None:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value,),
            details={"kind": "no_active_snapshot"},
        )

    age_ms = now_ms - int(active_snapshot_promoted_at_ms)
    tolerance_ms = freshness_tolerance_days * _MS_PER_DAY

    if age_ms > tolerance_ms:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value,),
            details={
                "kind": "snapshot_stale",
                "age_ms": age_ms,
                "age_days": age_ms / _MS_PER_DAY,
                "tolerance_days": freshness_tolerance_days,
            },
        )

    return _passing_result(
        details={
            "age_ms": age_ms,
            "age_days": age_ms / _MS_PER_DAY,
            "tolerance_days": freshness_tolerance_days,
        }
    )


# ---------------------------------------------------------------------------
# 10. KnowledgeUIVocabularyCoverage
#
# Asserts the /knowledge UI exposes the 5 expected tabs and every
# top-order in the gold has at least one row in the live top_orders
# table. Pairs with capability_graph_taxonomy_gold.
# ---------------------------------------------------------------------------


_EXPECTED_KNOWLEDGE_TABS: tuple[str, ...] = (
    "corpus",
    "top-orders",
    "meta-skills",
    "extensions",
    "refresh-history",
)
_EXPECTED_KNOWLEDGE_ENDPOINTS: tuple[str, ...] = (
    "/api/knowledge/corpus",
    "/api/knowledge/top-orders",
    "/api/knowledge/meta-skills",
    "/api/knowledge/extensions",
    "/api/knowledge/extensions/{extension_id}/provenance",
    "/api/knowledge/refresh-history",
    "/api/knowledge/health",
)


@register_scorer(
    skill_id="brickvision_runtime.capability_graph",
    name="KnowledgeUIVocabularyCoverage",
)
def knowledge_ui_vocabulary_coverage(
    *,
    observed_tab_ids: Sequence[str],
    observed_top_order_ids: Sequence[str],
    observed_endpoint_paths: Sequence[str] = (),
) -> ScorerResult:
    """Asserts the /knowledge UI exposes 5 tabs + 7 API endpoints,
    and that every gold top-order is present in the live taxonomy.

    Inputs:
      * ``observed_tab_ids`` — the ``TABS[].id`` array from
        ``apps/console/src/routes/knowledge.tsx``.
      * ``observed_top_order_ids`` — every ``TopOrderRow.top_order_id``
        from the live ``<BV_CATALOG>.<BV_SCHEMA>.top_orders``.
      * ``observed_endpoint_paths`` — optional; the FastAPI route
        paths from
        ``apps/console-api/src/console_api/routers/knowledge.py``.
        If empty, the endpoint coverage check is skipped (e.g.,
        offline tests that only have access to the front-end).

    Per the user's directive ("knowledge data that is indexed must
    be visible in the UI by sections"): a missing tab, a missing
    endpoint, or a missing top-order all indicate an indexed-but-
    invisible data section.
    """

    violations: list[dict[str, Any]] = []

    observed_tab_set = {str(t) for t in observed_tab_ids}
    expected_tab_set = set(_EXPECTED_KNOWLEDGE_TABS)
    missing_tabs = sorted(expected_tab_set - observed_tab_set)
    if missing_tabs:
        violations.append({"kind": "missing_tabs", "tab_ids": missing_tabs})
    extra_tabs = sorted(observed_tab_set - expected_tab_set)
    if extra_tabs:
        violations.append({"kind": "extra_tabs", "tab_ids": extra_tabs})

    if observed_endpoint_paths:
        observed_ep_set = {str(p) for p in observed_endpoint_paths}
        expected_ep_set = set(_EXPECTED_KNOWLEDGE_ENDPOINTS)
        missing_eps = sorted(expected_ep_set - observed_ep_set)
        if missing_eps:
            violations.append({"kind": "missing_endpoints", "paths": missing_eps})

    observed_to_set = {str(t) for t in observed_top_order_ids}
    gold_to_set = {row.top_order_id for row in SEED_TOP_ORDER_GOLD}
    missing_top_orders = sorted(gold_to_set - observed_to_set)
    if missing_top_orders:
        violations.append(
            {
                "kind": "missing_top_orders",
                "top_order_ids": missing_top_orders,
            }
        )

    if violations:
        return ScorerResult(
            score=0.0,
            reason_codes=(ReasonCode.DOCS_SECTION_ALIAS_MISSING.value,),
            details={
                "violations": _truncate(violations),
                "tab_count_observed": len(observed_tab_set),
                "tab_count_expected": len(expected_tab_set),
                "top_order_count_observed": len(observed_to_set),
                "top_order_count_gold": len(gold_to_set),
                "meta_skill_count_gold": len(SEED_META_SKILL_GOLD),
            },
        )

    return _passing_result(
        details={
            "tab_count": len(observed_tab_set),
            "top_order_count": len(observed_to_set),
            "endpoint_check_skipped": not bool(observed_endpoint_paths),
        }
    )


# ---------------------------------------------------------------------------
# Module exports — names mirror docs/23 §23.5 PascalCase scorer names
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _ScorerRef:
    """Lightweight reference to a registered scorer for caller use.

    Used by :func:`scorer_index` so test code and the eval harness
    can iterate the v0.7.7 scorer set without re-importing each
    scorer by name.
    """

    pascal_name: str
    snake_name: str
    reason_code: ReasonCode


_V0_7_7_SCORERS: tuple[_ScorerRef, ...] = (
    _ScorerRef(
        "CapabilityGraphSchemaIntegrity",
        "capability_graph_schema_integrity",
        ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED,
    ),
    _ScorerRef(
        "CapabilityGraphSmokeTestPassRate",
        "capability_graph_smoke_test_pass_rate",
        ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION,
    ),
    _ScorerRef(
        "IndexerDAGTaskSpec",
        "indexer_dag_task_spec",
        ReasonCode.CAPABILITY_GRAPH_PROMOTION_FAILED,
    ),
    _ScorerRef(
        "BudgetNamespaceIsolation",
        "budget_namespace_isolation",
        ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED,
    ),
    _ScorerRef(
        "ServicePrincipalIsolation",
        "service_principal_isolation",
        ReasonCode.INDEXER_SP_NOT_PROVISIONED,
    ),
    _ScorerRef(
        "VectorSearchEndpointGrants",
        "vector_search_endpoint_grants",
        ReasonCode.VS_ENDPOINT_GRANTS_MIXED,
    ),
    _ScorerRef(
        "SourceAuthorityAssignment",
        "source_authority_assignment",
        ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED,
    ),
    _ScorerRef(
        "HandAuthoredSkillExemplarLinkage",
        "hand_authored_skill_exemplar_linkage",
        ReasonCode.HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF,
    ),
    _ScorerRef(
        "HandAuthoredSkillAnchorGrounding",
        "hand_authored_skill_anchor_grounding",
        ReasonCode.HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED,
    ),
    _ScorerRef(
        "IndexerRefreshSLO",
        "indexer_refresh_slo",
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE,
    ),
    _ScorerRef(
        "KnowledgeUIVocabularyCoverage",
        "knowledge_ui_vocabulary_coverage",
        ReasonCode.DOCS_SECTION_ALIAS_MISSING,
    ),
)


def scorer_index() -> tuple[_ScorerRef, ...]:
    """Return the canonical v0.7.7 scorer index tuple.

    The eval harness uses this to iterate the 11 scorers without
    importing them by name. Order matches docs/23 §23.5.
    """

    return _V0_7_7_SCORERS


__all__ = [
    "budget_namespace_isolation",
    "capability_graph_schema_integrity",
    "capability_graph_smoke_test_pass_rate",
    "hand_authored_skill_anchor_grounding",
    "hand_authored_skill_exemplar_linkage",
    "indexer_dag_task_spec",
    "indexer_refresh_slo",
    "knowledge_ui_vocabulary_coverage",
    "scorer_index",
    "service_principal_isolation",
    "source_authority_assignment",
    "vector_search_endpoint_grants",
]
