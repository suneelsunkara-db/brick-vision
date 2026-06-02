"""Gold-set fixtures for the v0.7.7 Capability Graph scorers
(per docs/23-databricks-capability-graph.md §23.5).

Each gold set is the **deterministic ground truth** that a scorer
compares the live system state against. Two of them are also the
seed data the install step writes into the corresponding closed
Delta table; the other two live only in code (the live system is
self-describing for those checks).

Why these are 4 (not, say, 10)
==============================

Per §23.5 the 4 gold sets pair 1:1 with the 4 **structural
invariants** of the Capability Graph:

  1. Retrieval quality (smoke baseline) — query → expected ext_id
  2. Taxonomy completeness — top-orders ⊇ {7 named}, meta-skills
     ⊇ {32 named}
  3. Hand-authored linkage integrity — every ``SKILL.yaml`` file's
     ``exemplar_of`` value is well-formed (``meta:<m>/ext:<e>``)
  4. Indexer DAG topology — the 13-task DAG matches §23.3.1

The other 6 scorers (SchemaIntegrity, BudgetNamespaceIsolation,
ServicePrincipalIsolation, VectorSearchEndpointGrants,
SourceAuthorityAssignment, IndexerRefreshSLO) verify against
**immutable constants** in code (the 13 DDL strings, the locked
authority weights, the SP-name format), so they don't need a gold
set per se — the scorer's own assertions ARE the ground truth.

Drift contract
==============

If a gold set drifts from the live system (e.g., the developer
adds a new top-order to ``graph_builder._TOP_ORDERS`` without
updating ``SEED_TOP_ORDER_GOLD``), the scorer will surface a
non-passing Finding with the appropriate reason code at the next
scorer run. That's the **point** of having an orthogonal gold set:
it's a separately-curated witness that the canonical constants
weren't silently changed.
"""

from __future__ import annotations

import dataclasses


# ---------------------------------------------------------------------------
# Gold set 1: skill catalog (smoke baseline seeds)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CapabilityGraphSkillCatalogGoldRow:
    """One row in ``<BV_CATALOG>.<BV_SCHEMA>.smoke_baseline`` (§23.3.3).

    The install step writes ``SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD``
    into that table; the scorer compares the live table against this
    seed to detect drift.
    """

    query_id: str
    query_text: str
    expected_top_1_extension_id: str
    baseline_hit_rate: float


# Locked at v0.7.7 ship per §23.3.3. Any changes require a §23.5
# review + signed Claim from the substrate team.
SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD: tuple[
    CapabilityGraphSkillCatalogGoldRow, ...
] = (
    CapabilityGraphSkillCatalogGoldRow(
        query_id="cg-q1",
        query_text="create a delta table with table properties",
        expected_top_1_extension_id="meta:delta-lake/ext:create-table",
        baseline_hit_rate=0.80,
    ),
    CapabilityGraphSkillCatalogGoldRow(
        query_id="cg-q2",
        query_text="list catalogs in unity catalog",
        expected_top_1_extension_id="meta:unity-catalog-foundation/ext:list-catalogs",
        baseline_hit_rate=0.80,
    ),
    CapabilityGraphSkillCatalogGoldRow(
        query_id="cg-q3",
        query_text="register a model with mlflow tracking",
        expected_top_1_extension_id="meta:model-registry/ext:register-model",
        baseline_hit_rate=0.80,
    ),
    CapabilityGraphSkillCatalogGoldRow(
        query_id="cg-q4",
        query_text="create a multi-task databricks job",
        expected_top_1_extension_id="meta:lakeflow-jobs/ext:create-job",
        baseline_hit_rate=0.80,
    ),
    CapabilityGraphSkillCatalogGoldRow(
        query_id="cg-q5",
        query_text="transpile snowflake sql to databricks sql with lakebridge",
        expected_top_1_extension_id="meta:migration-transpile/ext:run-transpiler",
        baseline_hit_rate=0.80,
    ),
)


# ---------------------------------------------------------------------------
# Gold set 2: taxonomy (7 top-orders + 32 meta-skills)
#
# Mirrors graph_builder._TOP_ORDERS and graph_builder._META_SKILLS.
# The scorer asserts the live constants are a SUPERSET of these
# (additions are fine; removals are a regression).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TopOrderGoldRow:
    """One of the 7 v0.7.7 top-order skills (§23.2.4)."""

    top_order_id: str
    title: str


SEED_TOP_ORDER_GOLD: tuple[TopOrderGoldRow, ...] = (
    TopOrderGoldRow("to:data-engineering",  "Data Engineering"),
    TopOrderGoldRow("to:data-governance",   "Data Governance"),
    TopOrderGoldRow("to:data-ingestion",    "Data Ingestion"),
    TopOrderGoldRow("to:data-modelling",    "Data Modelling"),
    TopOrderGoldRow("to:machine-learning",  "Machine Learning"),
    TopOrderGoldRow("to:gen-ai",            "Generative AI"),
    TopOrderGoldRow("to:migration",         "Migration"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class MetaSkillGoldRow:
    """One of the seed v0.7.7 meta-skills (§23.2.5)."""

    meta_skill_id: str
    parent_top_order_id: str


# Locked at v0.7.7 ship; mirrors graph_builder._META_SKILLS.
# Total: 7 + 5 + 3 + 3 + 5 + 6 + 4 = 33 meta-skills.
SEED_META_SKILL_GOLD: tuple[MetaSkillGoldRow, ...] = (
    # ---- to:data-engineering (7) ----
    MetaSkillGoldRow("meta:delta-lake",                       "to:data-engineering"),
    MetaSkillGoldRow("meta:lakeflow-jobs",                    "to:data-engineering"),
    MetaSkillGoldRow("meta:lakeflow-declarative-pipelines",   "to:data-engineering"),
    MetaSkillGoldRow("meta:databricks-sql",                   "to:data-engineering"),
    MetaSkillGoldRow("meta:structured-streaming",             "to:data-engineering"),
    MetaSkillGoldRow("meta:compute",                          "to:data-engineering"),
    MetaSkillGoldRow("meta:workspace-administration",         "to:data-engineering"),
    # ---- to:data-governance (5) ----
    MetaSkillGoldRow("meta:unity-catalog-foundation",         "to:data-governance"),
    MetaSkillGoldRow("meta:uc-volumes",                       "to:data-governance"),
    MetaSkillGoldRow("meta:identity-and-access",              "to:data-governance"),
    MetaSkillGoldRow("meta:lineage-and-audit",                "to:data-governance"),
    MetaSkillGoldRow("meta:row-column-security",              "to:data-governance"),
    # ---- to:data-ingestion (3) ----
    MetaSkillGoldRow("meta:auto-loader",                      "to:data-ingestion"),
    MetaSkillGoldRow("meta:lakeflow-connect",                 "to:data-ingestion"),
    MetaSkillGoldRow("meta:dbsql-ingestion",                  "to:data-ingestion"),
    # ---- to:data-modelling (3) ----
    MetaSkillGoldRow("meta:dimensional-modelling",            "to:data-modelling"),
    MetaSkillGoldRow("meta:naming-conventions",               "to:data-modelling"),
    MetaSkillGoldRow("meta:constraints-and-keys",             "to:data-modelling"),
    # ---- to:machine-learning (5) ----
    MetaSkillGoldRow("meta:mlflow-experiments",               "to:machine-learning"),
    MetaSkillGoldRow("meta:mlflow-tracking",                  "to:machine-learning"),
    MetaSkillGoldRow("meta:feature-store",                    "to:machine-learning"),
    MetaSkillGoldRow("meta:model-registry",                   "to:machine-learning"),
    MetaSkillGoldRow("meta:model-serving",                    "to:machine-learning"),
    # ---- to:gen-ai (6) ----
    MetaSkillGoldRow("meta:mosaic-ai-vector-search",          "to:gen-ai"),
    MetaSkillGoldRow("meta:foundation-model-apis",            "to:gen-ai"),
    MetaSkillGoldRow("meta:agent-frameworks",                 "to:gen-ai"),
    MetaSkillGoldRow("meta:mosaic-ai-gateway",                "to:gen-ai"),
    MetaSkillGoldRow("meta:mlflow-prompt-registry",           "to:gen-ai"),
    MetaSkillGoldRow("meta:mlflow-tracing",                   "to:gen-ai"),
    # ---- to:migration (4) ----
    MetaSkillGoldRow("meta:migration-assessment",             "to:migration"),
    MetaSkillGoldRow("meta:migration-analysis",               "to:migration"),
    MetaSkillGoldRow("meta:migration-transpile",              "to:migration"),
    MetaSkillGoldRow("meta:migration-validation",             "to:migration"),
)


# ---------------------------------------------------------------------------
# Gold set 3: hand-authored skill exemplar links (15 SKILL.yaml entries)
#
# Mirrors the values stamped into ``skills/<id>/SKILL.yaml`` during
# Phase C.0 (per §23.2.6). Note: meta-skill IDs in these pointers
# may NOT be members of ``SEED_META_SKILL_GOLD`` — Phase C.0
# explicitly allows hand-authored skills to point at "minted"
# meta-skills (e.g., ``meta:row-column-security``) that are produced by
# the indexer at runtime but absent from the static taxonomy. The
# scorer's job is to validate **structural** integrity (well-formed
# pointer + the SKILL.yaml file exists), not enforce membership in
# the static taxonomy.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class HandAuthoredExemplarLinkGoldRow:
    """One ``SKILL.yaml`` ``exemplar_of`` pointer — the ground truth."""

    skill_id: str
    """The SKILL.yaml's directory name (e.g., ``ml.serve-deploy``)."""
    exemplar_of: str
    """Always shape ``meta:<m>/ext:<e>``."""


SEED_HAND_AUTHORED_EXEMPLAR_LINK_GOLD: tuple[
    HandAuthoredExemplarLinkGoldRow, ...
] = (
    HandAuthoredExemplarLinkGoldRow(
        skill_id="databricks.statement-execute",
        exemplar_of="meta:databricks-sql/ext:statement-execution-api",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="delta.pyspark-task-plan",
        exemplar_of="meta:lakeflow-jobs/ext:pyspark-transform-task-plan",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.serve-deploy",
        exemplar_of="meta:model-serving/ext:deploy-by-alias",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.assign-alias",
        exemplar_of="meta:model-registry/ext:assign-production-alias",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.train-evaluate-register",
        exemplar_of="meta:mlflow-tracking/ext:train-with-floor-and-register",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.problem-select",
        exemplar_of="meta:mlflow-tracking/ext:select-modeling-strategy",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.feature-readiness",
        exemplar_of="meta:mlflow-tracking/ext:feature-label-readiness",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.strategy-plan",
        exemplar_of="meta:mlflow-tracking/ext:databricks-ml-strategy-plan",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.model-family-select",
        exemplar_of="meta:mlflow-tracking/ext:ml-model-family-select",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.training-backend-probe",
        exemplar_of="meta:mlflow-tracking/ext:databricks-ml-training-backend-probe",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.training-backend-select",
        exemplar_of="meta:mlflow-tracking/ext:databricks-ml-training-backend-select",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.training-task-plan",
        exemplar_of="meta:mlflow-tracking/ext:databricks-ml-training-task-plan",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.api-plan-bind",
        exemplar_of="meta:mlflow-tracking/ext:databricks-api-plan-bind",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="ml.training-artifact-plan",
        exemplar_of="meta:mlflow-tracking/ext:databricks-ml-training-artifact-plan",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="delta.sql-transform",
        exemplar_of="meta:lakeflow-declarative-pipelines/ext:sql-transform-with-expectations",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="delta.pyspark-transform",
        exemplar_of="meta:lakeflow-declarative-pipelines/ext:pyspark-transform-with-expectations",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="docs.lookup",
        exemplar_of="meta:agent-frameworks/ext:fetch-doc-passages-on-demand",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="harness.workspace-claim-emitter",
        exemplar_of="meta:agent-frameworks/ext:emit-workspace-claims-to-kg",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="uc.tag-taxonomy-design",
        exemplar_of="meta:row-column-security/ext:design-domain-taxonomy",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="uc.catalog-bootstrap-design",
        exemplar_of="meta:unity-catalog-foundation/ext:bootstrap-catalog-design",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="lineage.introspect",
        exemplar_of="meta:lineage-and-audit/ext:introspect-table-feed-graph",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="uc.grant-recommend",
        exemplar_of="meta:identity-and-access/ext:recommend-minimum-grants",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="uc.grant-introspect",
        exemplar_of="meta:identity-and-access/ext:introspect-effective-grants",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="delta.table-layout-recommend",
        exemplar_of="meta:delta-lake/ext:recommend-table-layout",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="delta.table-introspect",
        exemplar_of="meta:delta-lake/ext:introspect-table-metadata",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="uc.catalog-introspect",
        exemplar_of="meta:unity-catalog-foundation/ext:introspect-catalog-tree",
    ),
    HandAuthoredExemplarLinkGoldRow(
        skill_id="lakeflow.jobs-run-submit",
        exemplar_of="meta:lakeflow-jobs/ext:jobs-runs-submit",
    ),
)


# ---------------------------------------------------------------------------
# Gold set 4: indexer DAG topology (13 tasks + dependencies)
#
# Mirrors databricks.yml resources.jobs.capability_indexer.tasks.
# The scorer parses the DAB YAML and asserts the live topology
# matches this gold set exactly (set-equality on dependencies).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IndexerDAGTaskGoldRow:
    """One task in the v0.7.7 capability_indexer DAG (§23.3.1).

    ``depends_on`` is the exact ``task_key`` set the task depends
    on — order-independent: the scorer compares as a set.
    """

    task_key: str
    depends_on: tuple[str, ...]


SEED_INDEXER_DAG_TOPOLOGY_GOLD: tuple[IndexerDAGTaskGoldRow, ...] = (
    IndexerDAGTaskGoldRow("plan",          ()),
    IndexerDAGTaskGoldRow("sdk",           ("plan",)),
    IndexerDAGTaskGoldRow("openapi",       ("plan",)),
    IndexerDAGTaskGoldRow("docs",          ("plan",)),
    IndexerDAGTaskGoldRow("blog",          ("plan",)),
    IndexerDAGTaskGoldRow("labs",          ("plan",)),
    IndexerDAGTaskGoldRow(
        "graph_builder",
        ("sdk", "openapi", "docs", "blog", "labs"),
    ),
    IndexerDAGTaskGoldRow("embed",         ("graph_builder",)),
    IndexerDAGTaskGoldRow("persist",       ("graph_builder",)),
    IndexerDAGTaskGoldRow("vs_upsert",     ("embed", "persist")),
    IndexerDAGTaskGoldRow("smoke",         ("vs_upsert",)),
    IndexerDAGTaskGoldRow("promote",       ("smoke",)),
    IndexerDAGTaskGoldRow("retention",     ("promote",)),
)


__all__ = [
    "CapabilityGraphSkillCatalogGoldRow",
    "HandAuthoredExemplarLinkGoldRow",
    "IndexerDAGTaskGoldRow",
    "MetaSkillGoldRow",
    "TopOrderGoldRow",
    "SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD",
    "SEED_HAND_AUTHORED_EXEMPLAR_LINK_GOLD",
    "SEED_INDEXER_DAG_TOPOLOGY_GOLD",
    "SEED_META_SKILL_GOLD",
    "SEED_TOP_ORDER_GOLD",
]
