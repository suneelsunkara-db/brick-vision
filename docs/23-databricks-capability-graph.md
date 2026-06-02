# 23 · The Databricks Capability Graph

**Covers:** §23.0 - §23.14 — the indexer-produced 3-level capability graph (Top-Order Skill → Meta-Skill → Extension) that supersedes the static skill-family projection as Stage A's primary retrieval source. Materialised by a multi-task serverless Job from a 5-source corpus (`databricks-sdk` AST · OpenAPI · per-cloud concept docs incl. Microsoft Learn · Databricks blog · Databricks Labs / Lakebridge), persisted in UC Delta tables + Mosaic AI Vector Search indexes, and consumed by Knowledge, usecase planning, and skill execution. The 27+ hand-authored SKILL.yaml exemplars are re-classified as named exemplar extensions inside their proper Top-Order.

**Related:** [`24-agent-operating-model.md`](./24-agent-operating-model.md) (agent/usecase/evaluation model), [`19-local-development.md`](./19-local-development.md) (operator bootstrap), [`22-changelog.md`](./22-changelog.md) (release decisions), [`../config/evaluation/README.md`](../config/evaluation/README.md) (evaluation datasets and scorer operations).

**Audience:** Anyone building, operating, or evaluating BrickVision's retrieval substrate. Anyone deciding whether a Databricks capability is in-scope for a partner's harness. Anyone debugging a Stage A retrieval miss.

**Status:** v0.7.9 (IMPLEMENTED + OPERATIONAL; source-grounding repair validated on May 15, 2026). The capability graph indexer is deployed and runs as a 14-task serverless Databricks Job (DAB-declared: 13 indexer tasks plus fatal Lakebase `sync`) under `bv_indexer_sp`, with structured graph tables in UC Delta, Mosaic AI Vector Search, Lakebase synced tables, and per-run JSON artifacts on a UC Volume for replay/debugging. The partner-side Console (`apps/console`) exposes `/knowledge` with semantic search, provenance drill-down, and grounded Q&A/code generation. The May 14 fact-finding pass identified the source-grounding defects below; the May 15 clean rerun repaired and validated them: active snapshot `snap_758295158481370` has `27/27` skill anchors resolved, `ungrounded=0`, 95 canonical OpenAPI operations, 0 OpenAPI duplicate keys, 0 `entity_edges`/`source_provenance` duplicate primary-key groups, and Lakebase `sync_verified=true` for the same snapshot. SQL, PySpark, Jobs, and ML skill chains now pass contract/runtime validation against source-grounded evidence; ML training remains intentionally gated on approved/generated training artifacts rather than custom BrickVision training code.

**Critical direction change (v0.7.9) — Capability Graph data-quality audit and repair.** A deep, multi-layer data-quality audit revealed systemic issues that were silently degrading retrieval quality and blocking ML/PySpark skill execution:

1. **`source_provenance` sync key was lossy.** The Lakebase synced-table primary key for `source_provenance` used only `(snapshot_id, entity_id, source_kind)`, collapsing 2,943 UC Delta rows to ~500 distinct keys. Provenance rows referencing different `ref` URLs or `content_hash` values for the same entity were silently deduplicated during sync. **Fix:** primary key expanded to `(snapshot_id, entity_id, source_kind, ref, content_hash)`.
2. **Operational tables were empty.** `corpus_health`, `refresh_plan`, and `source_authority` were never populated by the indexer — the `persist` task wrote only the 6 graph-structure tables. The Knowledge UI's Health tab showed stale/empty data. **Fix:** `run_capability_indexer.py` now constructs and passes `RefreshPlanRow`, `CorpusHealthRow`, and `SourceAuthorityRow` to `persist.persist_snapshot`.
3. **12 of 27 hand-authored skills had broken `exemplar_of` links.** Skills pointed to extensions that did not exist in the active graph snapshot — the gold set tracked only 15 skills while the repo contained 27. **Fix:** gold set expanded to all 27 skills; `HandAuthoredSkillExemplarLinkage` scorer now flags both missing and unexpected skills.
4. **`meta:databricks-sql` was missing from graph_builder.** The SQL statement-execution meta-skill was not declared, leaving `skill:databricks.statement-execute` orphaned. **Fix:** added `meta:databricks-sql` to `_META_SKILLS`.
5. **Persist was not idempotent on retry.** A task retry for the same snapshot appended duplicate rows instead of replacing. **Fix:** all persist writes now use `_replace_rows_by_key_via_sdk` (DELETE by key + INSERT) so retries are idempotent.
6. **Vector Search lacked `meta_skill_id` linkage.** Most retrieved documents had no `meta_skill_id` in their metadata, breaking auditable provenance chains from retrieval results back to the taxonomy.

**ML strategy direction change (v0.7.9):** The data-quality audit was triggered by repeated failures in ML skill execution. Root cause analysis revealed the failures were not in ML skill logic but in the retrieval substrate — ML skills could not find grounded Databricks API evidence because the graph lacked proper provenance, operational metadata, and skill-to-extension linkage. The corrective action is: **fix the data first, then let skills retrieve clean evidence, then ML execution flows naturally through the same skill-contract-driven pattern as SQL and PySpark — no custom ML code.** See `docs/24-agent-operating-model.md` §24.6.A for the revised ML execution strategy.

**May 14, 2026 verified source-grounding audit (current active snapshot `snap_61711220480415`):**

1. **The indexer ran successfully, but success did not mean source-grounding quality.** `refresh_plan_synced` shows planned sources `databricks-sdk-py`, `databricks-openapi-aws`, `databricks-docs-aws`, `databrickslabs-lakebridge`, and `databricks-blog`, with `partial_sources=[]` and `result_status=success`.
2. **OpenAPI/API-reference extraction staged data but graph routing dropped all of it.** `openapi_aws.json` contains 979 operations and 0 parse errors, but `graph_builder.json` reports `openapi_operations_routed=0`, and live `source_provenance_synced` has no `openapi` rows for the active snapshot. The staged operations are pseudo-operations derived from API reference HTML, with paths like `/api/workspace/statementexecution/executestatement` and `/api/workspace/jobs_21/submit`; graph routing expects versioned REST paths such as `/api/2.0/sql/statements` and `/api/2.1/jobs/runs/submit`.
3. **SDK evidence exists but lands on wrong or parallel extensions.** Examples from the active graph: `sql.StatementExecutionAPI.execute_statement` lands at `meta:compute/ext:execute-statement` while `skill:databricks.statement-execute` anchors to `meta:databricks-sql/ext:statement-execution-api`; `jobs.JobsAPI.submit` lands at `meta:lakeflow-jobs/ext:submit` while `skill:lakeflow.jobs-run-submit` anchors to `meta:lakeflow-jobs/ext:jobs-runs-submit`; `catalog.RegisteredModelsAPI.set_alias` lands at `meta:unity-catalog-foundation/ext:set-alias` while `skill:ml.assign-alias` anchors to `meta:model-registry/ext:assign-production-alias`.
4. **Hand-authored exemplar stubs masked source-grounding failures.** All 27 skills resolve to an extension, but all 27 anchors have only `source_kind=hand_authored`. `hand_authored` is contract provenance only; it must not count as capability evidence, graph authority, or execution grounding.
5. **All 27 `SKILL.yaml` manifests are missing `capability_links`.** `exemplar_of` declares the primary display/ranking anchor, but the planner needs `capability_links.primary` and `capability_links.uses` to understand composite skills and distinguish missing indexed artifacts from valid support evidence.

**May 15, 2026 operational validation (active snapshot `snap_758295158481370`):**

1. **Clean rerun completed end-to-end.** The generated Delta tables and staging artifacts were cleaned, the updated indexer was redeployed, and run `758295158481370` completed all tasks through `sync`.
2. **Canonicalization is now enforced before evidence emission.** API-reference aliases such as `workspace/jobs/...` and `workspace/jobs_21/...` collapse to one latest canonical operation while preserving account/workspace as separate control-plane dimensions. Product rename aliases such as DLT/Delta Live Tables/SDP route to the current Lakeflow Declarative Pipelines anchor instead of creating parallel capabilities.
3. **Primary-key quality gates passed.** The OpenAPI artifact emitted 95 operations with 0 duplicate operation keys; active Delta `entity_edges` and `source_provenance` both had 0 duplicate primary-key groups.
4. **Lakebase sync is now an end-to-end gate.** The final `sync` task reported `sync_verified=true`, `synced_snapshot_id=snap_758295158481370`, 10 tables created, 0 failures, and no sync errors.
5. **Skill evidence is source-grounded.** `scripts/validate_evidence_substrate.py` reported `27/27` skill anchors resolved, `missing=0`, `ungrounded=0`, `missing_capability_links=0`, and `primary_link_mismatches=0`.
6. **Execution-boundary skill validation passed.** SQL Statement Execution executed a grounded `SELECT 1`; PySpark planning plus Jobs submit executed a serverless validation job (`run_id=55381123498710`) and wrote one output row; the ML chain reached ready status through `ml.api-plan-bind` with Jobs submit and Statement Execution audit readback bound from indexed evidence. Full ML training remains gated on a generated/approved training artifact.

**Repair order (completed before SQL/PySpark/ML execution resumed):**

1. Replace or reclassify the OpenAPI acquisition path: either fetch real Databricks OpenAPI/reference artifacts with canonical methods, versioned paths, schemas, and request bodies, or create an explicit API-reference source adapter that canonicalizes docs reference pages into those fields. Do not treat sitemap HTML pages as already-canonical OpenAPI.
2. Fix SDK graph routing for fragmented services: SQL Statement Execution routes to `meta:databricks-sql`; registered-model/model-version APIs route to `meta:model-registry`; ML SDK methods split between MLflow experiments, MLflow tracking, model registry, feature store, and serving based on service class and method semantics.
3. Fix canonical extension identity: source-grounded operations must merge into the skill anchors, or the skill anchors must move to the source-grounded rows. There must not be parallel source rows and hand-authored-only stubs for the same real Databricks capability.
4. Add `capability_links` to every core skill, with `primary` including `exemplar_of` and `uses` listing supporting source-grounded SDK/OpenAPI/docs/labs extensions. Missing indexed artifacts are explicit gaps, not guessed links.
5. Keep validation fail-closed: `hand_authored` remains skill-contract/exemplar provenance only; evidence gates fail when anchors are `hand_authored`-only or when `capability_links` are absent.
6. Rerun the full indexer, inspect staging artifacts before promotion when possible, then require `scripts/validate_evidence_substrate.py --fail-on-quality-errors` to pass before attempting SQL/PySpark/ML execution.

The May 15 validation satisfied this repair order for `snap_758295158481370`. Future indexer changes must preserve these gates rather than bypassing them with custom skill logic.

Naming convention locked: `to:` (Top-Order Skill, closed; 5 IDs actual) · `meta:` (Meta-Skill, open; 14 IDs actual at v0.7.8, **33** at v0.7.9 after `meta:databricks-sql` addition and graph repair) · `ext:` (Extension, open; 467 IDs actual) · `stage:` (Build-Pipeline Stage, closed; was `meta:agent-*`) · `skill:` (Hand-authored exemplar, closed; **27** IDs at v0.7.9 — expanded from 15 at v0.7.8 to include SQL execution, PySpark planning, ML planning/strategy/training, and Jobs submission skills).

---

## §23.0 Scope and thesis

### §23.0.1 What this doc covers

A **single-source-of-truth specification** for the BrickVision capability graph: the empirical fact base of "what skills exist on Databricks today," materialised once per refresh from authoritative sources, queryable via Vector Search, browsable in the partner-side Console, signed for replay, and consumed by every Build-Pipeline Stage that needs to ground a design or a generated artifact in real Databricks behaviour.

It supersedes the *projection* in [`11-skill-catalog.md`](./11-skill-catalog.md) §9.3 ("21-family roadmap") because that projection was authored when BrickVision had no indexer; the projection assumed BrickVision would hand-author every Layer-1+ skill manually. With the indexer, that manual-curation assumption is no longer load-bearing — partners receive a populated capability graph the moment their indexer Job finishes its first refresh, and hand-authored skills become *exemplars* (high-quality reference implementations of an extension), not *the catalog*.

It does NOT cover (per [`01-overview.md`](./01-overview.md) §0 scope discipline):

- **The runtime build pipeline itself** — that lives in [`05-build-pipeline.md`](./05-build-pipeline.md). This doc only specifies the Stage A retrieval source change and the `stage:` rename.
- **The Build-Pipeline Stages' internal logic** — that lives in [`06-design-pipeline.md`](./06-design-pipeline.md), [`07-collaboration-and-loops.md`](./07-collaboration-and-loops.md), [`08-transpiler.md`](./08-transpiler.md). This doc specifies what they retrieve from, not how they reason.
- **The hand-authored Layer-0 skills' contracts** — those live in [`11-skill-catalog.md`](./11-skill-catalog.md) §9.1. This doc only specifies how they are re-classified inside the new taxonomy.
- **`stage:agent-productionize`'s Lakeflow + UC Bindings + alias semantics** — those live in [`11-skill-catalog.md`](./11-skill-catalog.md) §9.1 and [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.5. This doc is read-only with respect to those specs.

### §23.0.2 The thesis (why a capability graph at all)

Three load-bearing arguments:

1. **The Databricks surface is too large and too fast-moving to hand-curate.** Empirically (this doc, §23.1.1): `databricks-sdk` exposes ~120 services × ~756 methods × ~1,373 typed dataclasses across 21 sub-modules; the AWS-EN docs sitemap exposes 5,150 URLs across 85 top-level sections; the `/api/sitemap.xml` exposes ~600 endpoint pages across ~120 service nodes; the Databricks blog publishes new product-launch and pattern posts at the rate of several per week; Lakebridge alone covers six skill-bearing module groups (`transpiler`, `reconcile`, `assessments`, `connections`, `lineage`, `upgrades`) outside the core SDK. Hand-curation cannot keep up; a hand-curated catalog ages out of correctness within a release cycle.

2. **Stage A retrieval needs *structural* + *intent* + *provenance* signal in a single graph, not three.** Empirical evidence (this doc, §23.1.6): SDK alone covers ~95% of skill-bearing operations as method nodes but cannot tell Stage A *when* to use a method or *what user goal* a method serves; concept docs cover intent and cross-module relations the SDK cannot see (e.g., "use Lakehouse Monitoring on top of UC Tables for drift alerts" — no SDK call expresses this connection); blogs cover patterns the docs haven't ratified yet (e.g., "dbt-on-Databricks orchestration via Lakeflow"); Lakebridge covers migration entities the SDK doesn't model at all. A retrieval substrate that wants to ground every Stage A query needs all four classes (structural / intent / pattern / migration), in *one* graph, with provenance-tagged edges that let Stage A weight evidence by source authority.

3. **The graph must be partner-installable and partner-refreshable**, not a centrally hosted artifact, because: (a) BrickVision sells to partner SIs who deliver into customer workspaces with strict data-residency rules and frequent air-gap requirements; (b) cloud-specific docs (AWS / Azure / GCP / MS Learn) need to be indexed *per cloud* and Stage A must pick the right cloud's evidence based on the partner's `databricks_cloud` install variable; (c) refresh cadence is partner-controlled — some partners want nightly, some want weekly under change control; (d) the graph's signed snapshot becomes part of the replay envelope ([`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.3.1) so a build that referenced extension `meta:delta-lake/ext:merge` against snapshot `corpus_hash=ab12…` can be re-evaluated 18 months later against the same snapshot — no centrally hosted "latest" mode would survive that contract.

The capability graph is therefore a **partner-side, refreshable, signed, locally-queryable substrate** consumed at build time by every Stage and at browse time by the partner via the new `/knowledge` Console route.

### §23.0.3 Naming convention (locked)

| Concept | ID prefix | Cardinality (actual v0.7.8) | Source | Stage A weight |
|---|---|---|---|---|
| Top-Order Skill | `to:` | closed; **5** (`compute`, `data-engineering`, `machine-learning`, `governance`, `platform`) | hand-authored taxonomy (this doc §23.3.2) | 1.0 (taxonomy anchor) |
| Meta-Skill | `meta:` | open; **14** at v0.7.8 ship; grows with corpus | indexer-produced | per source authority (§23.1.6) |
| Extension | `ext:` (always namespaced under a meta-skill: `meta:<m>/ext:<e>`) | open; **467** at v0.7.8 ship; grows with corpus | indexer-produced | per source authority |
| Build-Pipeline Stage | `stage:` (was `meta:agent-*` pre-v0.7.7) | closed; 5 (`agent-design`, `agent-generate`, `agent-validate`, `agent-evaluate`, `agent-productionize`) | hand-authored runtime | n/a (consumer, not graph node) |
| Hand-authored exemplar | `skill:` (the existing Layer-0 IDs) | closed; **27** at v0.7.9 ship (was 15 at v0.7.8) — these define the **Agent Operating Model contracts** (see `docs/24-agent-operating-model.md` §24.4.2). Expanded in v0.7.9 to include SQL execution (`databricks.statement-execute`), PySpark planning (`delta.pyspark-task-plan`), ML planning/strategy/training chain (`ml.problem-select`, `ml.feature-readiness`, `ml.strategy-plan`, `ml.model-family-select`, `ml.training-backend-probe`, `ml.training-backend-select`, `ml.training-task-plan`, `ml.api-plan-bind`, `ml.train-evaluate-register`), and Jobs submission (`lakeflow.jobs-run-submit`, `lakeflow.jobs-run-poll`). | hand-authored | inherits from parent extension |

The renamed `stage:` prefix replaces the pre-v0.7.7 `meta:agent-*` ID space. The rename landed as a single sweep PR in v0.7.7 (D13 default; N174 — LANDED; one-shot rename across runtime, PRDs, tests, eval gold sets, and audit row payloads). Pre-v0.7.7 audit rows retain `meta:agent-*` strings as historical record; replay tooling treats `meta:agent-<id>` and `stage:agent-<id>` as alias-equivalent for backwards-compat reads only via `brickvision_runtime.core.actor_alias.normalize_actor_prefix()` — new writes always use `stage:`.

The `skill:` prefix on hand-authored Layer-0 skills is unchanged. Each `skill:<id>` carries a new optional field `exemplar_of: meta:<m>/ext:<e>` linking it into its parent extension; hand-authored skills not linked to a parent extension at v0.7.7 ship are migrated by a one-time mapping table (this doc §23.3.6) and the `exemplar_of` link becomes mandatory thereafter (enforced by the new `HandAuthoredSkillExemplarLinkage()` scorer, §23.8.2).

---

## §23.1 The five-source corpus

The Indexer ingests **five sources per refresh**, each with a distinct authority weight and refresh cadence. Per-source counts below are empirical from the v0.7.7 design exchange (`databricks-sdk==0.99.0`, `learn.microsoft.com` sitemap shards probed 2026-04, `docs.databricks.com/{aws,azure,gcp}/sitemap.xml` probed 2026-04, Databricks blog sitemap probed 2026-04, `databrickslabs/lakebridge@main` source tree probed 2026-04). Counts will drift; the Indexer's nightly refresh + `<bv>.capability_graph.corpus_health` table tracks drift.

### §23.1.1 Source 1 — `databricks-sdk` Python AST

The structural backbone. Every other source attaches edges to entities derived primarily from the SDK.

- **Adapter**: `brickvision_runtime/capability_graph/sources/sdk_adapter.py` (NEW v0.7.7).
- **Acquisition**: pinned `pip install databricks-sdk==<floor or higher>` in the indexer Job's serverless env (per [`19-local-development.md`](./19-local-development.md) §15.5; min floor `0.68.0` from N153).
- **Parsing**: Python `ast` walk of `databricks.sdk.service.*` (skip `_internal`); enumerate every `@dataclass`-decorated typed dataclass, every `*_api.py` service class, every public method on a service class.
- **Cardinality (empirical, `databricks-sdk==0.99.0`)**: 21 sub-modules · 120 service classes (94 from `WorkspaceClient` + 26 from `AccountClient`) · 756 methods · 1,373 typed dataclasses · 0 enums (Databricks SDK uses string literals + class-level constants, not Python `Enum`s, with the load-bearing exception of `catalog.Privilege` which has 45 string-valued members).
- **Output node kinds**: `sdk_module`, `sdk_service`, `sdk_method`, `sdk_dataclass`, `sdk_field`.
- **Output edge kinds**: `sdk_method.belongs_to → sdk_service`, `sdk_service.belongs_to → sdk_module`, `sdk_method.consumes → sdk_dataclass` (request type), `sdk_method.produces → sdk_dataclass` (response type), `sdk_method.paginates → sdk_method` (the `…_iter` peer if present), `sdk_method.deprecates → sdk_method` (where the docstring contains the `Deprecated` admonition with a successor reference).
- **Effect-class classification**: every method is assigned `effect_class ∈ {read, write, write·hitl, unclassified}` by a verb-stem heuristic (`get/list/describe/read/fetch/download → read`; `create/update/delete/put/post/patch/run/start/stop/cancel/grant/revoke → write`; `delete/destroy/uninstall/promote_alias_…/grant → write·hitl` if the method's resource type is in the closed `<bv>.policy.production_aliases` set per [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.5; otherwise `unclassified` and a `CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN` Question is emitted with the method ID for human review). Empirically (`databricks-sdk==0.99.0`): 278 read · 382 write · ~96 write·hitl (subset of write filtered by production_aliases scope) · 126 unclassified at first run; the 126 unclassified resolve over the first 4 weeks of human-review iteration to ~10 (the ones that legitimately defy the heuristic — typically `…_compute_compatibility` or `…_validate_*` style methods).
- **Authority weight**: 1.0 (highest; this is what every other source attaches to).
- **Refresh trigger**: nightly + on every new `databricks-sdk` release (PyPI version-feed polling).
- **Reason codes (NEW)**: `CAPABILITY_GRAPH_SDK_PARSE_FAILED` (hard fail, aborts DAG; previous active snapshot stays in place), `CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN` (per-method, human review).

### §23.1.2 Source 2 — OpenAPI

Effect-class evidence and per-method authentication / payload schemas the Python SDK normalizes away.

- **Adapter**: `brickvision_runtime/capability_graph/sources/openapi_adapter.py`.
- **Acquisition**: HTTPS GET against `https://docs.databricks.com/api/<path>/openapi.json` (the per-API-version OpenAPI artifacts the docs site already publishes). Cached in `<bv>.capability_graph.snapshots/openapi/<version>/` for replay.
- **Parsing**: standard OpenAPI 3.x walk; cross-link each `operationId` to the SDK method whose `__databricks_path__` matches (the SDK already carries the OpenAPI operationId in a `__databricks_path__` class attribute on most service classes).
- **Cardinality (empirical)**: ~600 endpoint operations across the `2.0/`, `2.1/`, `2.2/` API surfaces (matches the ~600 endpoint pages found in the `/api/sitemap.xml` probe).
- **Output node kinds**: `openapi_operation`, `openapi_schema`, `openapi_security_scheme`.
- **Output edge kinds**: `openapi_operation.implements → sdk_method` (the keystone cross-link; ~95% coverage), `openapi_operation.requires_scope → openapi_security_scheme`, `openapi_operation.uses_schema → openapi_schema`, `openapi_operation.under_path → <api_path_root>` (e.g., `/api/2.1/jobs`).
- **Effect-class refinement**: OpenAPI's `x-databricks-effect-class` extension (where present, ~70% of operations) overrides the SDK adapter's heuristic verb-stem classification.
- **Authority weight**: 0.95 (slightly below SDK because OpenAPI lags occasionally — empirically ~3-5 endpoints per quarter ship in the SDK before they appear in OpenAPI).
- **Refresh trigger**: nightly.
- **Reason codes (NEW)**: `CAPABILITY_GRAPH_OPENAPI_FETCH_FAILED` (per-version, soft fail; partial snapshot ships), `CAPABILITY_GRAPH_OPENAPI_SDK_LINK_MISSING` (per-operation, soft fail; surfaces as a `Question` with the operationId for SDK-team triage).

### §23.1.3 Source 3 — Concept docs (per-cloud, 4 corpora)

Intent + cross-module connections + docs-only entities. **Per directive 2 (v0.7.7 design exchange) — Microsoft Learn is in-scope from day 1, not deferred to v0.7.8.** Four corpora total:

| Corpus | Sitemap root | Approx URL count | Authority |
|---|---|---|---|
| `docs.databricks.com/aws/en/` | `/aws/sitemap.xml` | 5,150 | 0.85 |
| `docs.databricks.com/azure/en/` | `/azure/sitemap.xml` | 4,800 | 0.85 |
| `docs.databricks.com/gcp/en/` | `/gcp/sitemap.xml` | 3,900 | 0.85 |
| `learn.microsoft.com` Azure Databricks shard | `/azure/databricks/sitemap.xml` (or its mega-sitemap shard) | 4,200 | 0.80 |

- **Adapter**: `brickvision_runtime/capability_graph/sources/docs_adapter.py`. Cloud-tagged: each crawled URL carries `corpus_cloud ∈ {aws, azure, gcp, mslearn}`.
- **Acquisition**: BFS sitemap crawl → HTTPS GET each URL with rate limit (4 req/s default; respects `Retry-After`); body cached in `<bv>.capability_graph.snapshots/docs/<corpus>/<url-hash>.html.gz` for replay.
- **Skill-bearing filter**: empirically ~70% of docs URLs are skill-bearing after filtering out `release-notes/*`, `error-messages/*`, `archive/*`, and `index`-style landing pages.
- **Parsing**: HTML → Markdown via `BeautifulSoup4` + targeted CSS selectors; each docs page becomes a sequence of typed chunks (header chunk, narrative chunks, code-fence chunks, table chunks, admonition chunks). Chunk size: 1,500 tokens with 150-token overlap, boundary-aware on heading tags (same chunking grammar as `skill:docs.lookup` per [`11-skill-catalog.md`](./11-skill-catalog.md) §9.1.1, intentionally — so a docs URL fetched via the runtime `skill:docs.lookup` and the same URL discovered by the indexer produce alignable chunk indices).
- **Mention extraction**: per chunk, one structured-output call to the `kg_extractor` symbolic role ([`13-model-routing-and-budget.md`](./13-model-routing-and-budget.md) §11.1) — same closed kind vocabulary as `skill:docs.lookup` plus the new kinds `top_order_skill`, `meta_skill`, `extension`, `cross_cutting_axis`, `cloud_variance_marker`. The model is asked, against each chunk, "which capability-graph entities does this chunk reference?" — outputs are deterministic-equality-matched against existing nodes; matches become `MENTIONS` edges; unmatched mentions become `Question`s of kind `unresolved_mention` with the evidence span.
- **Output node kinds**: `docs_page`, `docs_chunk`, `docs_section_root` (the top-level path segment, e.g., `delta/`, `machine-learning/`, `mlflow3/`).
- **Output edge kinds**: `docs_chunk.under → docs_page`, `docs_page.under → docs_section_root`, `docs_chunk.mentions → <any-node>` (the load-bearing cross-link; PPR walks rely on this to hop from a user's natural-language goal into the structural backbone), `docs_section_root.aligns_with → meta_skill` (the docs-section ↔ meta-skill alias table; this doc §23.3.7).
- **Cloud variance handling**: an extension `meta:<m>/ext:<e>` that has docs in 4 corpora carries 4 `docs_chunk.mentions →` edges, one per corpus_cloud, each weighted by the partner's installed cloud (the partner's `databricks_cloud` install variable is read from `<bv>.config.partner` and the matching corpus's edges get weight 1.0; non-matching corpora get weight 0.3, retained for cross-cloud comparison searches but de-prioritized for default Stage A retrieval).
- **Authority weight**: 0.85 (slightly below OpenAPI because docs intent statements are occasionally aspirational ahead of the API; e.g., a doc might describe a feature in Public Preview before its OpenAPI / SDK landing).
- **Refresh trigger**: daily (docs are the most volatile of the structural sources).
- **Reason codes (NEW)**: `CAPABILITY_GRAPH_DOCS_FETCH_FAILED` (per-URL, soft fail; URL-level retry up to 3× then skipped with the snapshot ships partial), `CAPABILITY_GRAPH_DOCS_PARSE_FAILED` (per-page, soft fail), `CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL` (per-corpus, snapshot ships with `partial_sources: [<corpus>]`).

### §23.1.4 Source 4 — Databricks blog (NEW v0.7.7, per directive 2)

Pattern + product-launch + freshness signal. **Fills the Top-Order 3 (Data Modelling & Automation Design) gap** that SDK + concept docs alone could not fill — Genie / Lakeview / dbt / semantic-layer content lives mostly in the blog corpus.

- **Adapter**: `brickvision_runtime/capability_graph/sources/blog_adapter.py`.
- **Acquisition**: BFS crawl of `https://www.databricks.com/blog/sitemap.xml` (single global corpus; the blog is not per-cloud). Rate-limited at 2 req/s (lower than docs because the blog is more aggressively cached at CDN and aggressive rates trigger 429).
- **Filter (D12 default — allowlist + LLM scorer)**:
  - **Allowlist** (cheap): keep posts whose URL category prefix is in `{engineering-blog, mosaic-research, data-warehousing, data-engineering, mosaic-ai, delta-lake, unity-catalog, genie, dashboards, databricks-apps, mlflow}`. Drop posts in `{customer-stories, news, events, culture, partner-announcements}`.
  - **LLM scorer** (per remaining post): structured-output classification from the `kg_extractor` symbolic role with prompt "is this post skill-bearing? does it describe an API, a pattern, a configuration, or a concept relevant to building agent harnesses?" returning `is_skill_bearing: bool` + `confidence: float ∈ [0,1]` + `inferred_meta_skills: list[str]`. Posts with `is_skill_bearing=True ∧ confidence ≥ 0.7` are kept.
  - **Empirical keep rate**: ~25% of crawled posts (≈ 60-100 posts per quarter at 2026 publishing rates).
- **Skill-bearing share**: ~25% (post-filter, by design — see allowlist above).
- **Parsing**: same chunking grammar as docs (1,500 tokens / 150-token overlap, heading-aware). Each blog post carries `published_at`, `last_updated_at`, `author_handles[]`, `category_tags[]` metadata.
- **Recency decay (D14 default — 365-day half-life)**: every blog-derived edge carries weight `0.5 × 0.5^(age_days / 365)` at retrieval time. A post older than 5 years (1,825 days) decays to weight `0.5 × 0.03 ≈ 0.015` — effectively dropped from default retrieval, but still present in the graph for explicit "show me historical evidence" queries.
- **Output node kinds**: `blog_post`, `blog_chunk`.
- **Output edge kinds**: `blog_chunk.under → blog_post`, `blog_chunk.mentions → <any-node>` (same kg_extractor pipeline as docs; same closed kind vocabulary), `blog_post.tagged_with → meta_skill` (the LLM scorer's `inferred_meta_skills` output, deterministic-equality matched against existing meta_skill nodes; unmatched inferences emit `Question`s of kind `blog_meta_skill_inference_failed`).
- **Authority weight**: 0.50 base, decayed by recency. Blog edges never override SDK or docs evidence; they only fill gaps where structural evidence is absent.
- **Refresh trigger**: daily.
- **Reason codes (NEW)**: `CAPABILITY_GRAPH_BLOG_FETCH_FAILED` (per-URL, soft fail), `CAPABILITY_GRAPH_BLOG_FILTER_REJECTED_HIGH_VOLUME` (if > 80% of crawled posts are filtered out in a single refresh — likely an allowlist drift; emits a `Question` for human-review), `BLOG_META_SKILL_INFERENCE_FAILED` (per-post, soft fail).

### §23.1.5 Source 5 — Databricks Labs / Lakebridge (NEW v0.7.7, per directive 2)

Migration-side meta-skills the core SDK doesn't model. **Fills the Top-Order 5 (Migration & Ingestion) gap** that SDK + docs alone could not fill — Lakebridge is a separate `databrickslabs/*` package with its own SDK shape.

- **Adapter**: `brickvision_runtime/capability_graph/sources/labs_repo_adapter.py`. Configured for Lakebridge in v0.7.7; designed to scale to UCX, DQX, lsql, blueprint, mosaic in v0.7.8 (D10 default — defer non-Lakebridge labs packages).
- **Acquisition**:
  - `pip install databricks-labs-lakebridge==<latest>` in the indexer Job's serverless env (PyPI presence verified — the package is published).
  - `git clone --depth 1 https://github.com/databrickslabs/lakebridge.git` into the indexer task's ephemeral working directory for docs/markdown harvest (the clone is discarded at task end; nothing persists to UC).
- **Parsing**: same Python AST walk as the SDK adapter, applied to `databricks.labs.lakebridge.*`. Plus a Markdown crawl of `lakebridge/docs/` and the docusaurus site at `databrickslabs.github.io/lakebridge`.
- **Module → meta-skill mapping** (empirical, from `databrickslabs/lakebridge@main/src/databricks/labs/lakebridge/`):

| Lakebridge module | Becomes meta-skill | Notes |
|---|---|---|
| `transpiler/` | `meta:lakebridge-transpiler` | SQL dialect → Databricks SQL |
| `reconcile/` | `meta:lakebridge-reconciler` | source ↔ target row/column/aggregation parity |
| `assessments/` | `meta:lakebridge-assessor` | workload sizing, cost model, migration plan |
| `connections/` | `meta:lakebridge-connector` | Teradata, Snowflake, Redshift, Synapse, Oracle, Netezza adapters |
| `lineage.py` | `meta:lakebridge-migration-lineage` | harvest source-system lineage → materialize to UC |
| `upgrades/` | `meta:lakebridge-upgrades` | post-migration schema/code upgrades |
| `cli.py`, `install.py`, `uninstall.py` | `meta:lakebridge-harness` | install/uninstall extensions only |
| `analyzer/`, `intermediate/`, `coverage/`, `deployment/`, `helpers/`, `contexts/`, `errors/`, `resources/` | (not surfaced) | internal scaffolding; matches the SDK-adapter rule that internal/utility modules don't become meta-skills |

- **Output node kinds**: same as SDK adapter (`labs_module`, `labs_service`, `labs_method`, `labs_dataclass`, `labs_field`) plus `labs_repo`. Disambiguated from SDK nodes by the `source.kind=labs` field on every node.
- **Output edge kinds**: same as SDK adapter; plus `labs_method.cites_sdk → sdk_method` where Lakebridge's source code calls into core `databricks-sdk` (the keystone cross-link; ~60% of Lakebridge methods cite at least one SDK method).
- **Authority weight**: 0.75 (auxiliary SDK; below core SDK and OpenAPI but above docs and well above blogs).
- **Refresh trigger**: nightly (cheaper than full SDK refresh because Lakebridge is much smaller).
- **D10 deferral note**: UCX and DQX would each fill additional gaps (UCX → Top-Order 5 expansion + Top-Order 7 grants migration; DQX → Top-Order 2 quality patterns). The `LabsRepoAdapter` is shape-compatible (configurable via `(github_repo, pypi_name, module_path)`), so v0.7.8 wire-up is config-only, no new code.
- **Reason codes (NEW)**: `CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED` (hard fail for the Lakebridge task; soft fail for the snapshot — partial ships), `CAPABILITY_GRAPH_LABS_MODULE_UNKNOWN` (a new Lakebridge module appeared between refreshes that's not in the module → meta-skill map; emits a `Question` for human-review and the module's nodes are tagged `meta_skill_assignment_pending`).

### §23.1.6 Source authority order

When Stage A's PPR walk lands on competing evidence (two paths to the same answer with different source authorities), authority order breaks ties. **Highest first:**

1. `databricks-sdk` (1.00)
2. OpenAPI (0.95)
3. Concept docs — partner's installed cloud (0.85)
4. Lakebridge labs SDK (0.75)
5. Concept docs — non-installed cloud (0.30 after cloud weighting)
6. Databricks blog (0.50 base, decayed by recency; effective weight typically 0.10 - 0.50)

This ordering is encoded in `<bv>.capability_graph.source_authority` (a closed-set Delta table seeded once at install time, manually editable only by the harness owner per [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.4 — not partner-tunable per-engagement). New sources added in future releases bump the table's schema version (`source_authority.schema_version`); replay against an older snapshot uses the older schema_version's ordering, so a build that ranked evidence using v1 ordering will rank it the same way at replay time.

**Stage A retrieval contract**: `kg_search` ([`14-context-engineering.md`](./14-context-engineering.md) §11.5.1.A) returns ranked seeds; ranks are a product of PPR score × source authority weight × (recency decay if blog) × (cloud match if docs). Ties broken by node ID lexicographic order for determinism.

---

## §23.2 The three-level taxonomy

### §23.2.1 The hierarchy

```
to:<top-order>            ← closed; 7 IDs hand-authored (this doc §23.2.2)
  └── meta:<m>            ← open; indexer-produced; ~33 at v0.7.7 ship
        └── meta:<m>/ext:<e>   ← open; indexer-produced; ~750+ at v0.7.7 ship
              └── skill:<id>   ← optional hand-authored exemplar (the existing 15)
```

Every Extension belongs to exactly one Meta-Skill (single-parent rule, enforced by indexer schema constraint). Every Meta-Skill belongs to exactly one Top-Order (single-parent rule). Every hand-authored Layer-0 skill that exists at v0.7.7 ship is mapped to exactly one Extension by the migration table in §23.2.6 below; the `exemplar_of: meta:<m>/ext:<e>` field on `SKILL.yaml` becomes the link.

The single-parent rule is intentional: cross-cutting capabilities (e.g., `meta:databricks-asset-bundles` spanning Architecture / Engineering / Modelling) attach to whichever Top-Order the *primary user goal* belongs to (Asset Bundles → Engineering, because the user goal is "deploy code"), and surface in the others via cross-cutting axes (§23.2.5) rather than via multi-parenting. This keeps the PPR graph acyclic at the taxonomy level while preserving multi-Top-Order discoverability.

### §23.2.2 The 7 Top-Order Skills (closed set)

Closed at v0.7.7 ship. Adding a new Top-Order is a major-version PRD change (versioned in `<bv>.capability_graph.top_orders.schema_version`).

| ID | Name | Charter | Approx meta-skill count at v0.7.7 ship |
|---|---|---|---|
| `to:data-architecture-design` | Data Architecture Design | Lakehouse foundation: catalogs, storage, networking, compute, identity scaffolding | 5 |
| `to:data-engineering-design` | Data Engineering Design | Delta tables, Lakeflow pipelines, jobs, transformations | 6 |
| `to:data-modelling-automation-design` | Data Modelling & Automation Design | Genie, AI/BI dashboards, semantic layers, dbt-on-Databricks | 8 |
| `to:ai-agent-design-harness-engineering` | AI Agent Design & Harness Engineering | RAG, Agent Framework, Vector Search, Mosaic AI Gateway, Apps | 7 |
| `to:migration-ingestion` | Migration & Ingestion | Data into the lakehouse + migration from legacy systems (Lakebridge-heavy) | 13 |
| `to:ml-lifecycle` | ML Lifecycle | Train / register / deploy / monitor traditional (non-agent) ML | 6 |
| `to:governance-finops` | Governance & FinOps | Permissions, policies, billing, monitoring, lineage, audit, clean rooms | 9 |

Total at v0.7.7 ship: **~54 meta-skills** (sum of column 4). The earlier "~33" estimate from the v0.7.7 design exchange was the count *before* the directive-2 corpus expansion (blog + Lakebridge); after the expansion, especially in `to:migration-ingestion` (Lakebridge alone adds 7) and `to:data-modelling-automation-design` (blog adds 4), the count grows to ~54. Both numbers (~33 baseline · ~54 with v0.7.7 corpus expansion) are tracked in `<bv>.capability_graph.corpus_health` so drift is observable.

### §23.2.3 The ~54 Meta-Skills, listed

Per Top-Order, with primary source(s) and authority class. **Primary source** is the source that provides the meta-skill's structural backbone; **secondary sources** add intent / patterns / cloud variance and are not listed exhaustively below.

#### `to:data-architecture-design` (5 meta-skills)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:unity-catalog-foundation` | databricks-sdk (`catalog.{catalogs, schemas, external_locations, storage_credentials, connections, system_schemas}`) | high |
| `meta:workspace-provisioning` | databricks-sdk (`provisioning.*` account-level) | high |
| `meta:compute-platform` | databricks-sdk (`compute.*`) | high |
| `meta:workspace-bindings` | databricks-sdk (`catalog.workspace_bindings` + `iam.workspace_assignment`) | high |
| `meta:networking-connectivity` | databricks-sdk (`provisioning.{vpc_endpoints, private_access, networks}`) + per-cloud docs (high cloud variance) | high |

#### `to:data-engineering-design` (6 meta-skills)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:delta-lake` | databricks-sdk (`catalog.{tables, table_constraints}`) | high |
| `meta:lakeflow-declarative-pipelines` | databricks-sdk (`pipelines.*`) | high |
| `meta:workflows-jobs` | databricks-sdk (`jobs.{jobs, policy_compliance_for_jobs}`) | high |
| `meta:sql-warehouses` | databricks-sdk (`sql.{statement_execution, warehouses, queries, alerts}`) | high |
| `meta:pyspark-surface` | docs-only meta-capability (`docs/pyspark/` — 1,086 URLs); no single SDK service; pattern-extension-only | medium |
| `meta:databricks-asset-bundles` | docs + CLI (`databricks bundle …`); no SDK service | medium |

#### `to:data-modelling-automation-design` (8 meta-skills, 4 are blog-led per directive 2)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:genie` | databricks-sdk (`dashboards.genie`) + docs | high |
| `meta:lakeview-dashboards` | databricks-sdk (`dashboards.lakeview`) + docs | high |
| `meta:legacy-dashboards-queries` | databricks-sdk (`sql.{dashboards, queries, alerts}`) + docs | high |
| `meta:notebooks-repos` | databricks-sdk (`workspace.{workspace, repos, git_credentials}`) + docs | high |
| `meta:semantic-modeling-on-databricks` (NEW v0.7.7) | **blog** + docs/lakehouse-architecture | medium |
| `meta:dbt-on-databricks` (NEW v0.7.7) | **blog** + docs/partners/dbt + dbt-databricks PyPI | medium |
| `meta:bi-tool-integration` (NEW v0.7.7) | docs/integrations + **blog** | medium |
| `meta:analytics-engineering-patterns` (NEW v0.7.7) | **blog** | low (blog-only; flagged as "exploratory" with Authority badge `low` in the Knowledge UI) |

#### `to:ai-agent-design-harness-engineering` (7 meta-skills)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:model-serving` | databricks-sdk (`serving.serving_endpoints`) + docs | high |
| `meta:mosaic-ai-vector-search` | databricks-sdk (`vectorsearch.{endpoints, indexes}`) + docs | high |
| `meta:foundation-model-apis` | docs-only meta-capability over `serving.serving_endpoints_data_plane` + the FMS endpoint catalog | high |
| `meta:mosaic-ai-gateway` | databricks-sdk (`serving.serving_endpoints.put_ai_gateway`) + docs/generative-ai/ai-gateway | high |
| `meta:agent-frameworks` | docs-only + `mlflow.deployments` + `databricks-agents` PyPI | high |
| `meta:databricks-apps` | databricks-sdk (`apps.apps`) + docs | high |
| `meta:online-tables-lakebase` | databricks-sdk (`catalog.online_tables`) + docs/oltp | high |

#### `to:migration-ingestion` (13 meta-skills, 7 are Lakebridge per directive 2)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:lakeflow-connect` | databricks-sdk (`catalog.connections`) + docs | high |
| `meta:auto-loader-copy-into` | docs (patterns) + `pipelines` SDK | high |
| `meta:delta-sharing` | databricks-sdk (`sharing.*`) + docs | high |
| `meta:marketplace` | databricks-sdk (`marketplace.*`) + docs | high |
| `meta:files-volumes` | databricks-sdk (`files.*`, `catalog.volumes`) + docs | high |
| `meta:query-federation` | databricks-sdk (`catalog.connections` foreign catalogs) + docs/query-federation | high |
| `meta:lakebridge-transpiler` (NEW v0.7.7) | **lakebridge** (`transpiler/`) | high |
| `meta:lakebridge-reconciler` (NEW v0.7.7) | **lakebridge** (`reconcile/`) | high |
| `meta:lakebridge-assessor` (NEW v0.7.7) | **lakebridge** (`assessments/`) | high |
| `meta:lakebridge-connector` (NEW v0.7.7) | **lakebridge** (`connections/`) | high |
| `meta:lakebridge-migration-lineage` (NEW v0.7.7) | **lakebridge** (`lineage.py`) | high |
| `meta:lakebridge-upgrades` (NEW v0.7.7) | **lakebridge** (`upgrades/`) | high |
| `meta:lakebridge-harness` (NEW v0.7.7) | **lakebridge** (`cli.py`, `install.py`, `uninstall.py`) | high |

#### `to:ml-lifecycle` (6 meta-skills)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:mlflow-tracking` | databricks-sdk (`ml.experiments`) + docs/mlflow3 | high |
| `meta:uc-registered-models` | databricks-sdk (`catalog.{registered_models, model_versions}`) + docs | high |
| `meta:model-registry` | databricks-sdk (`catalog.registered_models.{set,delete}_registered_model_alias`) + docs | high |
| `meta:workspace-model-registry-deprecated` | databricks-sdk (`ml.model_registry`) + docs/manage-model-lifecycle/workspace-model-registry | high (with `IS_DEPRECATED_BY → meta:uc-registered-models` edge surfaced in Knowledge UI) |
| `meta:lakehouse-monitoring` | databricks-sdk (`catalog.quality_monitors`) + docs/lakehouse-monitoring | high |
| `meta:feature-engineering` | databricks-sdk (`catalog.online_tables`) + docs/feature-store + Feature Spec docs (docs-only) | high |

#### `to:governance-finops` (9 meta-skills)

| Meta-Skill | Primary source | Authority |
|---|---|---|
| `meta:identity-and-access` | databricks-sdk (`catalog.grants` + `iam.permissions`) | high |
| `meta:system-tables` | docs-only — `system.{access.audit, billing.usage, lineage.*, compute.*, serving.endpoint_usage}` | high |
| `meta:billing-budgets` | databricks-sdk (`billing.{billable_usage, budgets, log_delivery, usage_dashboards}`) | high |
| `meta:service-principals-tokens` | databricks-sdk (`iam.{service_principals, users, groups}` + `oauth2.*`) | high |
| `meta:secrets` | databricks-sdk (`workspace.secrets`) + docs/security/secrets | high |
| `meta:clean-rooms` | databricks-sdk (`cleanrooms.*`) + docs/clean-rooms | high |
| `meta:lineage-and-audit` | docs-only — `system.lineage.*` system tables + Catalog Explorer | high |
| `meta:row-column-security` | databricks-sdk (`catalog.{tag_policies, workspace_entity_tag_assignments}`) + docs/tags | high |
| `meta:ip-access-lists` | databricks-sdk (`iam.ip_access_lists` + account-level `ip_access_lists`) + docs/security/network/ip-access-list | high |

### §23.2.4 Extensions (size estimate, ID format)

Extension IDs are always namespaced under their parent meta-skill: `meta:<m>/ext:<e>`.

The `<e>` slug is generated from the SDK method name (or OpenAPI operationId, or docs-pattern slug), normalized to lowercase-hyphen. Examples:

- `meta:delta-lake/ext:create-table` (SDK: `tables.create`)
- `meta:delta-lake/ext:add-table-constraint` (SDK: `table_constraints.create`)
- `meta:lakebridge-transpiler/ext:transpile-from-teradata` (Lakebridge pattern)
- `meta:dbt-on-databricks/ext:dbt-orchestrated-by-lakeflow` (blog pattern; Authority `medium`)
- `meta:system-tables/ext:audit-query-template-pii-access` (docs pattern)

**Cardinality estimate at v0.7.7 ship**: ~750 extensions, distributed roughly proportionally to method counts of the underlying SDK services (Top-Order 7 governance has the most extensions because `iam.*` + `catalog.grants` together expose ~80 methods; Top-Order 5 migration has the second-most because Lakebridge contributes ~150 patterns + 50 SDK extensions). Specifically:

| Top-Order | Estimated extensions at v0.7.7 ship |
|---|---|
| `to:data-architecture-design` | ~80 |
| `to:data-engineering-design` | ~110 |
| `to:data-modelling-automation-design` | ~70 |
| `to:ai-agent-design-harness-engineering` | ~120 |
| `to:migration-ingestion` | ~200 |
| `to:ml-lifecycle` | ~80 |
| `to:governance-finops` | ~90 |

**Each extension carries**:

```yaml
id: meta:<m>/ext:<e>
parent_meta_skill: meta:<m>
parent_top_order: to:<top-order>
title: <human-readable title; LLM-generated, deterministic per chunk hash>
synopsis: <120-char synopsis; LLM-generated>
effect_class: read | write | write·hitl | unclassified
when_to_use: <human-readable trigger; LLM-generated from docs+blog evidence>
inputs: <typed schema; SDK-derived for SDK-backed exts; docs-derived for pattern-only>
outputs: <typed schema; SDK-derived or docs-derived>
sources:
  primary:   { kind, ref, hash, parsed_at }   # e.g., kind=sdk, ref="catalog.tables.create"
  secondary: [{ kind, ref, hash, parsed_at }, ...]
authority: high | medium | low                # max(source.weight) at materialize time
cloud_variance: invariant | aws-only | azure-only | gcp-only | per-cloud-overlay
lifecycle: ga | public-preview | beta | deprecated | removed
deprecates: meta:<m'>/ext:<e'> | null         # for IS_DEPRECATED_BY edges
deprecated_by: meta:<m'>/ext:<e'> | null      # inverse
min_sdk_version: <semver>                     # parsed from SDK method's first-introduced commit
exemplar_skill_id: skill:<id> | null          # the load-bearing link from §23.0.3
last_indexed_at: <iso8601>
last_indexed_corpus_hash: <hash>
```

### §23.2.5 Cross-cutting axes (orthogonal classifiers)

These appear inside *every* Top-Order. They become **tag dimensions on extensions**, not sibling Top-Orders. Every extension carries the cross-cutting axis values as fields (per the schema in §23.2.4):

- **Authoring surface**: `sdk` · `cli` · `terraform` · `dab` · `ui` · `notebook`
- **Effect class**: `read` · `write` · `write·hitl`
- **Cloud variance**: `invariant` · `aws-only` · `azure-only` · `gcp-only` · `per-cloud-overlay`
- **Lifecycle**: `ga` · `public-preview` · `beta` · `deprecated` · `removed`
- **Min SDK floor**: semver string

Stage A's `kg_search` query language ([`14-context-engineering.md`](./14-context-engineering.md) §11.5.1.A) extends with an optional `axis_filters: dict[str, str]` parameter at v0.7.7 (e.g., `{cloud_variance: "aws-only", effect_class: "write·hitl"}` to retrieve only AWS-only HITL-gated write operations). Default is no filter; partner installs whose `databricks_cloud` is set inject `cloud_variance ∈ {invariant, <partner-cloud>-only, per-cloud-overlay}` automatically (the explicit filter overrides the install-default).

### §23.2.6 The 27 hand-authored skills, re-classified (directive 1, expanded v0.7.9)

Per the v0.7.7 design exchange directive 1: "hand-authored skill must pickup from the Top order skills." Every Layer-0 skill in [`11-skill-catalog.md`](./11-skill-catalog.md) §9.1 is mapped to exactly one Extension. The `exemplar_of:` field on `SKILL.yaml` becomes mandatory in v0.7.7. **v0.7.9 expansion:** the original 15 skills grew to **27** as the execution layer added SQL execution, PySpark planning, the full ML skill chain, and Jobs submission skills.

| Hand-authored skill | Re-classified under | New `exemplar_of:` |
|---|---|---|
| `skill:uc.catalog-introspect` | `to:data-architecture-design` | `meta:unity-catalog-foundation/ext:introspect-catalog-tree` |
| `skill:delta.table-introspect` | `to:data-engineering-design` | `meta:delta-lake/ext:introspect-table-metadata` |
| `skill:delta.table-layout-recommend` | `to:data-engineering-design` | `meta:delta-lake/ext:recommend-table-layout` |
| `skill:uc.grant-introspect` | `to:governance-finops` | `meta:identity-and-access/ext:introspect-effective-grants` |
| `skill:uc.grant-recommend` | `to:governance-finops` | `meta:identity-and-access/ext:recommend-minimum-grants` |
| `skill:lineage.introspect` | `to:governance-finops` | `meta:lineage-and-audit/ext:introspect-table-feed-graph` |
| `skill:uc.catalog-bootstrap-design` | `to:data-architecture-design` | `meta:unity-catalog-foundation/ext:bootstrap-catalog-design` |
| `skill:uc.tag-taxonomy-design` | `to:governance-finops` | `meta:row-column-security/ext:design-domain-taxonomy` |
| `skill:harness.workspace-claim-emitter` | `to:ai-agent-design-harness-engineering` | `meta:agent-frameworks/ext:emit-workspace-claims-to-kg` |
| `skill:docs.lookup` | `to:ai-agent-design-harness-engineering` | `meta:agent-frameworks/ext:fetch-doc-passages-on-demand` |
| `skill:delta.pyspark-transform` | `to:data-engineering-design` | `meta:lakeflow-declarative-pipelines/ext:pyspark-transform-with-expectations` |
| `skill:delta.sql-transform` | `to:data-engineering-design` | `meta:lakeflow-declarative-pipelines/ext:sql-transform-with-expectations` |
| `skill:ml.train-evaluate-register` | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:train-with-floor-and-register` |
| `skill:ml.assign-alias` | `to:ml-lifecycle` | `meta:model-registry/ext:assign-production-alias` |
| `skill:ml.serve-deploy` | `to:ml-lifecycle` | `meta:model-serving/ext:deploy-by-alias` |
| `skill:databricks.statement-execute` (NEW v0.7.9) | `to:data-engineering-design` | `meta:databricks-sql/ext:statement-execution-api` |
| `skill:delta.pyspark-task-plan` (NEW v0.7.9) | `to:data-engineering-design` | `meta:lakeflow-jobs/ext:pyspark-transform-task-plan` |
| `skill:ml.problem-select` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:ml-problem-type-selection` |
| `skill:ml.feature-readiness` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:feature-engineering/ext:feature-readiness-check` |
| `skill:ml.strategy-plan` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:ml-strategy-plan` |
| `skill:ml.model-family-select` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:model-family-selection` |
| `skill:ml.training-backend-probe` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:training-backend-probe` |
| `skill:ml.training-backend-select` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:training-backend-selection` |
| `skill:ml.training-task-plan` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:training-task-plan` |
| `skill:ml.api-plan-bind` (NEW v0.7.9) | `to:ml-lifecycle` | `meta:mlflow-tracking/ext:api-plan-bind` |
| `skill:lakeflow.jobs-run-submit` (NEW v0.7.9) | `to:data-engineering-design` | `meta:lakeflow-jobs/ext:jobs-run-submit` |
| `skill:lakeflow.jobs-run-poll` (NEW v0.7.9) | `to:data-engineering-design` | `meta:lakeflow-jobs/ext:jobs-run-poll` |

**Coverage (v0.7.9):**

| Top-Order | Hand-authored exemplars at v0.7.9 |
|---|---|
| `to:data-architecture-design` | 2 (`uc.catalog-introspect`, `uc.catalog-bootstrap-design`) |
| `to:data-engineering-design` | 7 (`delta.table-introspect`, `delta.table-layout-recommend`, `delta.pyspark-transform`, `delta.sql-transform`, `databricks.statement-execute`, `delta.pyspark-task-plan`, `lakeflow.jobs-run-submit`, `lakeflow.jobs-run-poll`) |
| `to:data-modelling-automation-design` | **0** — gap |
| `to:ai-agent-design-harness-engineering` | 2 (`harness.workspace-claim-emitter`, `docs.lookup`) |
| `to:migration-ingestion` | **0** — gap |
| `to:ml-lifecycle` | 12 (`ml.train-evaluate-register`, `ml.assign-alias`, `ml.serve-deploy`, `ml.problem-select`, `ml.feature-readiness`, `ml.strategy-plan`, `ml.model-family-select`, `ml.training-backend-probe`, `ml.training-backend-select`, `ml.training-task-plan`, `ml.api-plan-bind`) |
| `to:governance-finops` | 4 (`uc.grant-introspect`, `uc.grant-recommend`, `lineage.introspect`, `uc.tag-taxonomy-design`) |

**v0.7.9 improvement:** `to:ml-lifecycle` expanded from 3 to 12 exemplars, making ML the most richly specified skill chain. The ML chain follows the same skill-contract-driven pattern as SQL and PySpark: each step in the ML lifecycle (problem selection → feature readiness → strategy → model family → backend probe → backend selection → task planning → API binding → training/evaluation/registration) is a separate contracted skill with typed inputs/outputs, not monolithic custom code. Top-Order 3 (Data Modelling) and Top-Order 5 (Migration & Ingestion) remain gaps for future expansion.

### §23.2.7 The docs-section ↔ meta-skill alias table

The fragmented SDK modules (`settings`, `iam`, `workspace`) need mapping rules to assign their methods to meta-skills, because the mechanical "SDK module = meta-skill" rule is not 1:1 for these three. The alias table lives in `<bv>.capability_graph.docs_section_aliases`:

| Docs section root | Mapped meta-skill |
|---|---|
| `delta/` | `meta:delta-lake` |
| `optimizations/` | `meta:delta-lake` |
| `pyspark/` | `meta:pyspark-surface` |
| `sql/` | `meta:sql-warehouses` |
| `ldp/` | `meta:lakeflow-declarative-pipelines` |
| `jobs/` | `meta:workflows-jobs` |
| `compute/` | `meta:compute-platform` |
| `dashboards/genie/` | `meta:genie` |
| `dashboards/` (excl. `/genie/`) | `meta:lakeview-dashboards` |
| `notebooks/` | `meta:notebooks-repos` |
| `dev-tools/databricks-apps/` | `meta:databricks-apps` |
| `dev-tools/git/` | `meta:notebooks-repos` |
| `dev-tools/bundles/` | `meta:databricks-asset-bundles` |
| `mlflow3/` | `meta:mlflow-tracking` |
| `machine-learning/foundation-models/` | `meta:foundation-model-apis` |
| `machine-learning/model-serving/` | `meta:model-serving` |
| `machine-learning/manage-model-lifecycle/` | `meta:uc-registered-models` |
| `machine-learning/manage-model-lifecycle/workspace-model-registry/` | `meta:workspace-model-registry-deprecated` |
| `machine-learning/feature-store/` | `meta:feature-engineering` |
| `generative-ai/vector-search/` | `meta:mosaic-ai-vector-search` |
| `generative-ai/ai-gateway/` | `meta:mosaic-ai-gateway` |
| `generative-ai/agent-framework/` | `meta:agent-frameworks` |
| `oltp/` | `meta:online-tables-lakebase` |
| `ingestion/lakeflow-connect/` | `meta:lakeflow-connect` |
| `ingestion/cloud-files/` | `meta:auto-loader-copy-into` |
| `ingestion/copy-into/` | `meta:auto-loader-copy-into` |
| `data-sharing/` | `meta:delta-sharing` |
| `marketplace/` | `meta:marketplace` |
| `files/` | `meta:files-volumes` |
| `query-federation/` | `meta:query-federation` |
| `migration/lakebridge/` | (split across the 7 lakebridge meta-skills via the module map in §23.1.5) |
| `clean-rooms/` | `meta:clean-rooms` |
| `data-governance/lakehouse-monitoring/` | `meta:lakehouse-monitoring` |
| `data-governance/binding/` | `meta:workspace-bindings` |
| `data-governance/data-lineage/` | `meta:lineage-and-audit` |
| `data-governance/tags/` | `meta:row-column-security` |
| `data-governance/` (excl. above) | `meta:identity-and-access` |
| `admin/system-tables/` | `meta:system-tables` |
| `admin/account-settings-e2/usage/` | `meta:billing-budgets` |
| `admin/account-settings-e2/` (excl. usage) | `meta:workspace-provisioning` |
| `admin/users-groups/` | `meta:service-principals-tokens` |
| `security/secrets/` | `meta:secrets` |
| `security/network/ip-access-list/` | `meta:ip-access-lists` |
| `security/network/` (excl. ip-access-list) | `meta:networking-connectivity` |
| `security/` (excl. above) | `meta:identity-and-access` |
| `partners/dbt/` | `meta:dbt-on-databricks` |
| `integrations/` | `meta:bi-tool-integration` |
| `lakehouse-architecture/` | `meta:semantic-modeling-on-databricks` |
| `release-notes/`, `error-messages/`, `archive/` | (excluded from indexing) |

The alias table is **closed at v0.7.7 ship**. Adding a new docs section root requires a PRD migration (versioned in `docs_section_aliases.schema_version`); during a refresh, any new docs section root (one that exists in the sitemap but not in the table) emits a `Question` of kind `docs_section_alias_missing` with the section name; chunks under that section are indexed as `meta_skill_assignment_pending` and excluded from default Stage A retrieval until human review fills in the alias.

---

## §23.3 The Indexer Job (multi-task serverless DAG)

Per directive 1 of the v0.7.7 design exchange: the Indexer runs as a **multi-task Databricks Serverless Job** in the partner's BrickVision workspace, isolated from the app by service principal, budget namespace, and UC schema, with proper error handling per task. Single Job, single name, scheduled cron-daily plus on-demand.

### §23.3.1 Job-level spec

| Field | Value |
|---|---|
| Job name | `bv_capability_indexer` |
| Tags | `{purpose: "indexer", owner: "brickvision", isolation: "high"}` |
| Service principal | `bv_indexer_sp` (separate from `bv_app_sp`; provisioned at install time per [`19-local-development.md`](./19-local-development.md) §15.5) |
| Budget namespace | `indexer.*` (separate from `app.*`; the existing `BudgetGuard` per [`13-model-routing-and-budget.md`](./13-model-routing-and-budget.md) §11.4 acquires its namespace from `BV_BUDGET_NAMESPACE` env var, defaults to `app` for app-side calls and is set to `indexer` for Job-side calls; the indexer cannot bleed into `app.*` budget — enforced by `BudgetNamespaceIsolation()` scorer in §23.8.2) |
| Compute | `serverless` (no shared cluster pollution) |
| Schedule | cron `0 2 * * *` UTC (D16 default — nightly at 02:00 UTC); on-demand via `databricks jobs run-now` or the new `brickvision indexer refresh` CLI |
| Max concurrent runs | 1 (refreshes never overlap; queued if scheduled trigger fires while a previous run is in-flight) |
| Timeout | 90 min (cold-cache full refresh empirically takes 35-50 min; nightly incremental refreshes 8-15 min — 90 min is the hard ceiling, beyond which the Job aborts and the previous active snapshot stays in place) |
| Email notifications | on-failure to `bv_indexer_sp.notification_email` (configured at install) |
| Webhook notifications | on-failure to BrickVision telemetry per [`15-platform-telemetry.md`](./15-platform-telemetry.md) §11.6 (anonymized event kind `indexer_failure`) |

### §23.3.2 DAG shape (15 tasks)

Each box is one task. Arrows are explicit `depends_on` edges. Task names are stable (the rename of `meta:agent-*` → `stage:agent-*` does not affect indexer task names).

```
                                    plan_refresh
                                    (computes corpus_hash baseline,
                                     decides which sources need refresh,
                                     reads <bv>.capability_graph.corpus_health
                                     for last-success per source)
                                          │
       ┌─────────┬─────────┬──────────────┼────────────┬─────────┬────────┐
       ▼         ▼         ▼              ▼            ▼         ▼        ▼
   crawl_sdk  crawl_doc  crawl_doc    crawl_doc   crawl_doc   crawl_   crawl_
              s_aws      s_azure      s_gcp       s_mslearn   blog     lakebridge
       │         │         │              │            │         │        │
       └─────────┴─────────┴──────┬───────┴────────────┴─────────┴────────┘
                                  ▼
                            extract_entities
                  (parallel-fan-out: 1 worker per source's payload;
                   runs kg_extractor structured-output calls;
                   merges into the entity_edges working table)
                                  │
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
            embed_batch     build_entity_graph    persist_to_uc
            (sharded;       (PPR edges +          (top_orders,
             1 shard per    cross-source links;   meta_skills,
             100 entities)  computes node           extensions,
                            degrees + PPR seeds)  entity_edges,
                  │                │              source_provenance,
                  ▼                │              corpus_snapshots
            upsert_vs_indexes      │              into <bv>.capability_graph.*)
            (3 indexes via         │                  │
             VS Delta Sync)        │                  │
                  │                │                  │
                  └────────┬───────┴──────────────────┘
                           ▼
                       smoke_test
              (5 canonical PPR queries from
               tests/fixtures/capability_graph/smoke_queries_v1.yaml;
               hit-rate ≥ 0.95 of previous baseline)
                           │
                           ▼
                     promote_snapshot
              (atomic flip of <bv>.capability_graph.active_snapshot_id;
               previous active snapshot remains in 30-day rollback window)
                           │
                           ▼
                   emit_telemetry_and_alerts
              (CAPABILITY_GRAPH_REFRESH_OK/PARTIAL/FAILED Question;
               telemetry event indexer_refresh per [`15-platform-telemetry.md`] §11.6;
               updates corpus_health with refresh_duration, embedding_cost,
               hit_rate_trend)
```

### §23.3.3 Per-task spec

#### `plan_refresh` (1 task)

- **Reads**: `<bv>.capability_graph.corpus_health` (per-source last-success timestamps), `<bv>.capability_graph.snapshots/` Volume listing (for snapshot retention check).
- **Writes**: `<bv>.capability_graph.refresh_plan` (one row per refresh; carries the planned source list, freshness thresholds, expected volumes).
- **Skip logic**: if every source's `last_success_at >= now - source_min_refresh_interval` AND no on-demand override is set, the task short-circuits and emits `CAPABILITY_GRAPH_REFRESH_SKIPPED_ALL_FRESH`; downstream tasks are marked `skipped`.
- **Compute**: tiny; 1 vCPU, 2 GB serverless slot.
- **Timeout**: 5 min.
- **Retries**: 2 (idempotent — the plan is recomputed each run from the same inputs).

#### `crawl_sdk` (1 task)

- **Hard-fail bottleneck.** If this task fails after retries, the DAG aborts (`smoke_test` never runs; previous active snapshot stays in place; `CAPABILITY_GRAPH_SDK_PARSE_FAILED` Question emitted with the parse error).
- **Reads**: `pip install databricks-sdk==<latest-or-pinned>` in the task's serverless env; resolves the actual installed version via `databricks.sdk.version.__version__` and pins it into `<bv>.capability_graph.refresh_plan.sdk_version`.
- **Writes**: `<bv>.capability_graph.snapshots/sdk/<version>.json.gz` (the parsed AST in the working snapshot).
- **Compute**: 4 vCPU, 8 GB serverless slot (the `ast.parse` walk over the SDK is RAM-heavy).
- **Timeout**: 15 min.
- **Retries**: 3 (idempotent — same `pip install` produces the same parse).

#### `crawl_docs_aws`, `crawl_docs_azure`, `crawl_docs_gcp`, `crawl_docs_mslearn` (4 tasks, parallel)

- **Reads**: per-corpus sitemap.xml; HTTPS GET each URL with rate limit `4 req/s` per task (i.e. up to 16 req/s aggregate across the four tasks).
- **Writes**: `<bv>.capability_graph.snapshots/docs/<corpus>/<url-hash>.html.gz`.
- **Skip logic**: per-URL skip when the previous snapshot's `etag` matches a HEAD request (cuts ~70% of fetches on incremental refreshes).
- **Soft-fail per URL**: 3 retries with `linear_backoff(60s)`; after retries the URL is logged and skipped, the task ships partial.
- **Soft-fail per corpus**: if > 20% of URLs in a single corpus fail to crawl, the task is marked `partial` in `<bv>.capability_graph.refresh_plan.partial_sources` and emits `CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL`; the snapshot ships partial, the DAG continues.
- **Hard-fail per corpus**: if the sitemap.xml itself returns non-200 for 3 consecutive retries, the task fails entirely; `CAPABILITY_GRAPH_DOCS_FETCH_FAILED` emitted; the corpus is excluded from this snapshot but the snapshot ships partial (other corpora can still complete).
- **Compute**: 2 vCPU, 4 GB per task (I/O-bound; CPU-light).
- **Timeout**: 25 min per task.
- **Retries**: 2 at task level (the per-URL retries are nested inside; task-level retry only fires for sitemap-level failures).

#### `crawl_blog` (1 task)

- **Reads**: `https://www.databricks.com/blog/sitemap.xml`; HTTPS GET with rate limit `2 req/s`.
- **Writes**: `<bv>.capability_graph.snapshots/blog/<url-hash>.html.gz`.
- **Filter** (D12 default — allowlist + LLM scorer): allowlist filtering is in-task (cheap); LLM scoring is offloaded to the `extract_entities` task because it shares the same `kg_extractor` pool.
- **Soft-fail**: same posture as docs (per-URL retry, partial OK).
- **Compute**: 2 vCPU, 4 GB.
- **Timeout**: 20 min.
- **Retries**: 2.

#### `crawl_lakebridge` (1 task)

- **Reads**: `pip install databricks-labs-lakebridge==<latest>` + `git clone --depth 1 https://github.com/databrickslabs/lakebridge.git` into the task's working volume.
- **Writes**: `<bv>.capability_graph.snapshots/lakebridge/<version>.json.gz` (parsed AST + harvested markdown docs).
- **Soft-fail at the DAG level**: if Lakebridge install or clone fails after 3 retries, the task fails but the DAG continues (other sources contribute their entities; the snapshot ships partial with `partial_sources: ["lakebridge"]`). `CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED` emitted.
- **Compute**: 4 vCPU, 8 GB (RAM for the AST walk, same as `crawl_sdk` shape).
- **Timeout**: 12 min.
- **Retries**: 3.

#### `extract_entities` (1 task with parallel-fan-out workers)

- **Reads**: all the crawl_* tasks' working snapshots from the Volume.
- **Writes**: `<bv>.capability_graph.entity_working` (a temporary table per refresh; dropped on snapshot promotion).
- **Per-source fan-out**: spawns 1 worker per source-payload-shard. SDK + Lakebridge are parsed in 1 worker each (small enough); docs corpora and blog are sharded by 100 chunks per worker. Workers run `kg_extractor` structured-output calls in parallel under a per-source budget cap (the budget cap prevents one runaway corpus from starving the others).
- **LLM model**: the `kg_extractor` symbolic role per [`13-model-routing-and-budget.md`](./13-model-routing-and-budget.md) §11.1 — the same role used by `skill:docs.lookup` for runtime mention extraction. The same closed kind vocabulary applies; the same `Question`s emit on unresolved mentions and vocabulary gaps.
- **Soft-fail**: per-chunk extraction failures (LLM timeout, malformed structured output) emit a `Question` per chunk and skip that chunk's mentions; the chunk's `CONTENT` Claim still lands (it's retrievable as a passage even without graph hops).
- **Hard-fail**: total extraction failures > 5% of chunks attempted abort the task; `CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED` emitted; the snapshot fails (no promotion).
- **Compute**: 8 vCPU, 16 GB (LLM call concurrency, not CPU; the bottleneck is the model serving endpoint's QPS).
- **Timeout**: 35 min.
- **Retries**: 1 (extraction LLM calls are deterministic-replayable per `BV_TRACE_TIME` per [`13-model-routing-and-budget.md`](./13-model-routing-and-budget.md) §11.5; one retry is enough — repeated retries indicate a deeper failure mode that needs operator intervention, not silent looping).

#### `embed_batch` (1 task with sharded execution)

- **Reads**: `<bv>.capability_graph.entity_working` extensions + meta-skills + chunks needing embedding.
- **Writes**: `<bv>.capability_graph.entity_working.embeddings` (vector column added in-place).
- **Sharding**: 1 shard per 100 entities; embeddings are computed via the partner's configured embedding endpoint (default: `databricks-gte-large-en` — a Foundation Model API, free under serverless serving).
- **Cache**: deterministic content-hash keyed cache in `<bv>.capability_graph.embedding_cache` — entities whose `content_hash` matches a cached entry skip re-embedding (~90% hit rate on incremental refreshes; ~5% on cold-cache full refreshes).
- **Soft-fail**: per-shard timeouts retry 3× with backoff; after retries the shard is logged and skipped (the entities in the shard land in the structured tables but not the VS index until the next refresh — emitted as `CAPABILITY_GRAPH_EMBEDDING_SHARD_FAILED` Question).
- **Compute**: 4 vCPU, 8 GB (I/O-bound on embedding endpoint).
- **Timeout**: 20 min.
- **Retries**: 1 at task level.

#### `build_entity_graph` (1 task)

- **Reads**: `<bv>.capability_graph.entity_working` (entities + edges so far).
- **Writes**: PPR seeds + node degrees back into `<bv>.capability_graph.entity_working`.
- **Logic**: computes the cross-source linkage graph (the SDK module ↔ docs section ↔ blog tags ↔ Lakebridge cites_sdk axes from §23.1), assigns each meta-skill its `to:<top-order>` parent via the `docs_section_aliases` table from §23.2.7 plus the SDK module map from §23.1.5, computes per-node PPR seed weights for the smoke test step.
- **Hard-fail**: any meta-skill without a `to:<top-order>` parent at the end of this step fails the task; `CAPABILITY_GRAPH_ORPHANED_META_SKILL` emitted with the meta-skill's ID; the snapshot fails (no promotion). This is intentional: orphaned meta-skills indicate alias-table drift that would silently skew Stage A retrieval.
- **Compute**: 4 vCPU, 8 GB (NetworkX in-memory graph computation; ~4k nodes, ~30k edges fits easily).
- **Timeout**: 15 min.
- **Retries**: 1.

#### `persist_to_uc` (1 task)

- **Reads**: `<bv>.capability_graph.entity_working` (final graph).
- **Writes**: 11 UC Delta tables + 1 Volume — the 6 graph-structure tables from §23.4.2 (`corpus_snapshots`, `top_orders`, `meta_skills`, `extensions`, `entity_edges`, `source_provenance`), the 3 operational tables (`source_authority`, `refresh_plan`, `corpus_health`), plus `embedding_cache` and the `snapshot.json.gz` on the staging Volume.
- **Atomicity**: new snapshot rows carry `snapshot_id = <new>`; readers join against `<bv>.capability_graph.active_snapshot_id` (atomically flipped in `promote_snapshot`). Until promotion, readers only see the previous snapshot.
- **Idempotency (v0.7.9 fix)**: every table write uses `_replace_rows_by_key_via_sdk` — a `DELETE` by key columns followed by `INSERT`. A task retry for the same `snapshot_id` first removes that snapshot's prior rows, then inserts the freshly rendered rows. This prevents duplicate rows on retry, which was a bug in v0.7.8 where appends could accumulate. Operational tables (`source_authority`, `corpus_health`) use non-snapshot keys (e.g., `schema_version + source_kind`, `recorded_at_ms + source_kind`) and are replaced by those keys.
- **Soft-fail**: any Delta write retry up to 3× with backoff. Hard-fail aborts the task; previous active snapshot stays.
- **Compute**: 2 vCPU, 4 GB.
- **Timeout**: 12 min.
- **Retries**: 2.

#### `upsert_vs_indexes` (1 task)

- **Reads**: the persisted Delta tables (`<bv>.capability_graph.{meta_skills, extensions, …}`).
- **Writes**: triggers the 3 Mosaic AI Vector Search indexes' `TRIGGERED` sync (per §23.5.3).
- **Wait**: blocks until VS reports `INDEX_STATUS = ONLINE` for all 3 indexes; polls every 30s up to the task timeout.
- **Soft-fail**: a single index failing to sync emits `CAPABILITY_GRAPH_VS_SYNC_FAILED` per index; the snapshot ships partial with `partial_sources: ["vs:<index_name>"]` — the structured tables are still queryable via SQL (degraded fallback path for Stage A; `kg_search` falls back to direct SQL against the structured tables when its preferred VS index is `partial`).
- **Compute**: 1 vCPU, 2 GB (purely orchestration; the VS sync runs in VS-side compute).
- **Timeout**: 30 min.
- **Retries**: 1.

#### `smoke_test` (1 task)

- **Reads**: 5 canonical PPR queries from `tests/fixtures/capability_graph/smoke_queries_v1.yaml` (D17 default — locked at v0.7.7 ship; refreshed quarterly via manual review).
- **Logic**: runs each query through the new snapshot's VS indexes via `kg_search`; compares hit-rate against the previous active snapshot's baseline (stored in `<bv>.capability_graph.smoke_baseline`).
- **Promotion gate**: hit-rate ≥ 0.95 × baseline → pass → proceed to `promote_snapshot`. Hit-rate < 0.95 × baseline → fail → snapshot marked `failed`, alarm fired, previous active snapshot stays in place. `CAPABILITY_GRAPH_SMOKE_REGRESSION` emitted with the failing queries' IDs.
- **First-refresh exception**: on the first ever refresh (no baseline), the task emits a baseline (records hit-rates for the 5 queries) and passes unconditionally. The first baseline becomes the "v1" baseline; subsequent refreshes compare against it. Quarterly review may re-baseline.
- **Compute**: 2 vCPU, 4 GB.
- **Timeout**: 8 min.
- **Retries**: 0 (smoke regression is not a transient failure; retrying would mask a real signal).

#### `promote_snapshot` (1 task)

- **Reads**: the new snapshot's metadata.
- **Writes**: atomically flips `<bv>.capability_graph.active_snapshot_id.snapshot_id` to the new snapshot's ID. Updates `<bv>.capability_graph.snapshot_history` with the new active snapshot + the deactivated previous snapshot (with `deactivated_at`).
- **Sign**: writes a SHA-256 content-hash digest of the snapshot's `content_hashes` to `<bv>.capability_graph.corpus_snapshots.signature` for tamper-evidence + replay-pinning. There is no cryptographic signing step — the digest is identity, not non-repudiation.
- **Compute**: 1 vCPU, 2 GB (single-row write; metadata only).
- **Timeout**: 3 min.
- **Retries**: 2 (idempotent — the same flip applied twice produces the same end state).

#### `emit_telemetry_and_alerts` (1 task)

- **Reads**: the refresh outcome from all upstream tasks.
- **Writes**:
  - `CAPABILITY_GRAPH_REFRESH_OK` Question with the snapshot ID (if all tasks succeeded).
  - `CAPABILITY_GRAPH_REFRESH_PARTIAL` Question with the partial sources list (if any task soft-failed but the DAG promoted).
  - `CAPABILITY_GRAPH_REFRESH_FAILED` Question with the failing task ID + error (if the DAG aborted before promotion; previous active snapshot remains).
  - Updates `<bv>.capability_graph.corpus_health` with `last_refresh_at`, `refresh_duration_ms`, `embedding_cost_usd`, `hit_rate` (smoke), `partial_sources`.
  - Emits anonymized telemetry event `indexer_refresh` per [`15-platform-telemetry.md`](./15-platform-telemetry.md) §11.6.
- **Compute**: 1 vCPU, 2 GB.
- **Timeout**: 3 min.
- **Retries**: 2 (idempotent).

### §23.3.4 Error-handling matrix (summary)

| Failure | Effect on this snapshot | Effect on active snapshot | Reason code emitted |
|---|---|---|---|
| `crawl_sdk` fails (after retries) | DAG aborts; no promotion | unchanged | `CAPABILITY_GRAPH_SDK_PARSE_FAILED` |
| `crawl_docs_<corpus>` fails (sitemap-level) | corpus excluded; snapshot ships partial | replaced (partial OK if smoke passes) | `CAPABILITY_GRAPH_DOCS_FETCH_FAILED` + `CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL` |
| `crawl_blog` fails (sitemap-level) | source excluded; snapshot ships partial | replaced (partial OK if smoke passes) | `CAPABILITY_GRAPH_BLOG_FETCH_FAILED` |
| `crawl_lakebridge` fails | source excluded; snapshot ships partial | replaced (partial OK if smoke passes) | `CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED` |
| `extract_entities` rate > 5% chunks failing | DAG aborts; no promotion | unchanged | `CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED` |
| `embed_batch` shard fails | entities land in tables but skip VS index until next refresh | replaced (degraded VS path) | `CAPABILITY_GRAPH_EMBEDDING_SHARD_FAILED` |
| `build_entity_graph` orphaned meta-skill | DAG aborts; no promotion | unchanged | `CAPABILITY_GRAPH_ORPHANED_META_SKILL` |
| `persist_to_uc` Delta write fails | DAG aborts; no promotion | unchanged | `CAPABILITY_GRAPH_PERSIST_FAILED` |
| `upsert_vs_indexes` per-index sync fails | snapshot ships partial; that index in degraded SQL-fallback mode | replaced (partial OK if smoke passes) | `CAPABILITY_GRAPH_VS_SYNC_FAILED` |
| `smoke_test` regression < 0.95 baseline | snapshot marked failed; no promotion | unchanged | `CAPABILITY_GRAPH_SMOKE_REGRESSION` |
| `promote_snapshot` flip fails | snapshot remains in failed-promotion state | unchanged (flip is atomic; either succeeded or didn't) | `CAPABILITY_GRAPH_PROMOTION_FAILED` |
| `emit_telemetry_and_alerts` fails | snapshot is promoted but telemetry is missing | replaced (promotion already happened) | `CAPABILITY_GRAPH_TELEMETRY_FAILED` |

The error-handling rule: **never silently substitute, never silently skip the smoke test, never silently roll back.** Failures are loud, and the previous active snapshot stays in place by default — making this a "no-action-needed-on-failure" posture for partner ops (the worst failure mode is "harness builds keep using the previous snapshot until human review," not "harness builds break").

### §23.3.5 Workspace isolation contract (no app impact)

| Isolation axis | Mechanism | Verifier |
|---|---|---|
| Compute | Indexer Job uses serverless compute exclusively; no shared cluster pollution. | `BudgetNamespaceIsolation()` scorer (§23.8.2) statically asserts the Job spec uses `compute: serverless`. |
| Service principal | Indexer uses `bv_indexer_sp`; app uses `bv_app_sp`. SPs have non-overlapping privilege sets: indexer-SP has WRITE on `<bv>.capability_graph.*` + READ on docs.databricks.com (via outbound HTTPS only); app-SP has READ-ONLY on `<bv>.capability_graph.*` + no docs.databricks.com access. | `ServicePrincipalIsolation()` scorer (§23.8.2). |
| Budget | Indexer's LLM calls flow through `BudgetGuard` with namespace `indexer`; app's flow through `app`. The two namespaces have separate quotas (per-day token caps + per-call caps). | `BudgetNamespaceIsolation()` scorer (§23.8.2). |
| Storage (UC) | Indexer writes `<bv>.capability_graph.*`; app writes `<bv>.builds.*` + `<bv>.kg.*` (existing). The two schemas have non-overlapping owner SPs. | UC's own `grants` enforcement; verified by `UCSchemaOwnership()` install pre-flight. |
| Vector Search endpoint | Shared endpoint `bv_vs_endpoint` (D15 default) hosts both the existing N117 indexes (`claims_text_index`, `doc_passages_index`) and the 3 new capability-graph indexes. Indexer-SP has WRITE on the 3 capability-graph indexes only; app-SP has READ on all 5. The shared endpoint reduces $/mo + simplifies networking; the SP-level WRITE/READ separation preserves isolation. | `VectorSearchEndpointGrants()` install pre-flight. |
| Network | Indexer Job's outbound HTTPS goes through the partner workspace's existing egress controls (per-cloud documented patterns). No new VPC endpoints required. | partner-side responsibility; documented in [`19-local-development.md`](./19-local-development.md) §15.5. |

### §23.3.6 Refresh cadence and scheduling

D16 default — nightly at 02:00 UTC. The exact UTC hour is partner-tunable via the `BV_INDEXER_SCHEDULE_CRON` install variable (default `0 2 * * *`); partners in different operational time zones can move it to e.g., `0 6 * * *` (06:00 UTC) without code changes.

Per-partner deployment tiers:

- **Tier 1: nightly + on-demand.** The default. Most partners. Cron schedule + ability to fire `databricks jobs run-now` (or the new `brickvision indexer refresh` CLI) ad-hoc.
- **Tier 2: on-demand only.** Partners under change-control regimes that don't tolerate scheduled jobs. The cron schedule is disabled at install time (`BV_INDEXER_SCHEDULE_DISABLED=true`); refreshes happen manually via the CLI.
- **Tier 3: weekly.** Partners that prioritize cost over freshness. Configured via `BV_INDEXER_SCHEDULE_CRON=0 2 * * 0` (Sunday 02:00 UTC); the smoke test's recency tolerance (`BV_INDEXER_FRESHNESS_TOLERANCE_DAYS`, default 2) extends to 8 days for these partners.

Partners can switch tiers at any time by re-running install (`brickvision install --resume-from indexer_schedule`).

---

## §23.4 Storage architecture (three tiers)

Per directive 2 of the v0.7.7 design exchange: indexed data lands in Mosaic AI Vector Search, with raw artifacts in a UC Volume and structured tables in UC Delta. The three tiers each have a distinct purpose and lifecycle.

### §23.4.1 Tier A — Raw signed snapshots in UC Volume

| Field | Value |
|---|---|
| Location | `<bv>.capability_graph.snapshots/` (UC Volume) |
| File format | gzipped JSON, one file per refresh: `snapshot_{corpus_hash}_{ts}.json.gz` |
| Per-source sub-volumes | `<bv>.capability_graph.snapshots/sdk/<version>/`, `<bv>.capability_graph.snapshots/docs/<corpus>/`, `<bv>.capability_graph.snapshots/blog/`, `<bv>.capability_graph.snapshots/lakebridge/<version>/`, `<bv>.capability_graph.snapshots/openapi/<version>/` |
| Mutability | immutable (write-once; `<bv>.policy.production_aliases` per [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.5 includes `capability_graph_snapshots`, blocking re-writes via `write·hitl` enforcement) |
| Signing | SHA-256 content-hash digest written to `corpus_snapshots.signature` for tamper-evidence + replay (no cryptographic signing layer) |
| Retention | 30 days (the previous-snapshot rollback window); after 30 days the snapshot is deleted by `<bv>.capability_graph.snapshot_retention_job` (a separate serverless Job, not part of the indexer DAG) |
| Estimated size | ~50-200 MB per refresh × 30 days = 1.5-6 GB total at steady state |
| Purpose | replay (every build's replay envelope references `corpus_snapshot_id`; replay reconstructs the snapshot's state from this Volume + the structured tables' snapshot-tagged rows) + audit (signed snapshots are tamper-evident; the partner's auditor can verify any historical snapshot's integrity offline) |

### §23.4.2 Tier B — Structured tables in UC Delta

Schema: `<bv>.capability_graph.*` (the new schema introduced in v0.7.7; provisioned at install time per [`19-local-development.md`](./19-local-development.md) §15.5).

| Table | Purpose | Approx row count at steady state |
|---|---|---|
| `top_orders` | the closed 7 (versioned per snapshot) | 7 × N (where N = retained snapshot count) |
| `meta_skills` | ~54 at v0.7.7 ship; one row per meta-skill per snapshot | ~54 × N |
| `extensions` | ~750 at v0.7.7 ship; one row per extension per snapshot | ~750 × N |
| `entity_edges` | typed cross-refs (cites, derives, deprecates, sibling, mentions) for PPR | ~3-5k × N |
| `source_provenance` | for each entity: source URL/file/line/commit-SHA/parsed_at | one row per entity per source × N |
| `corpus_snapshots` | one row per refresh; tracks `snapshot_id`, `corpus_hash`, `partial_sources`, `signed_by`, `signature`, `promoted_at`, `deactivated_at` (NULL while active) | one row per refresh |
| `corpus_health` | rolling SLO data (refresh duration, embedding cost, hit-rate trend) | one row per refresh × source |
| `active_snapshot_id` | single-row pointer table; readers join against this to see only the active snapshot | 1 row, mutated atomically by `promote_snapshot` |
| `source_authority` | the closed-set source authority weights from §23.1.6 | 6 rows × schema_version |
| `docs_section_aliases` | the closed-set docs-section ↔ meta-skill alias table from §23.2.7 | ~50 rows × schema_version |
| `embedding_cache` | content-hash keyed embedding cache (cuts ~90% of embed calls on incremental refreshes) | rows accumulate; pruned by retention to ~10k entries |
| `smoke_baseline` | the locked-at-v0.7.7-ship hit-rate baseline from §23.3.3 | 5 rows |
| `refresh_plan` | one row per refresh; planned source list + freshness thresholds | one row per refresh |

All tables carry `snapshot_id` as their first column (or `_partition_id`); reads always JOIN against `active_snapshot_id` so only the active snapshot's rows are visible by default. Replay against an older snapshot uses an explicit `WHERE snapshot_id = <historical>` predicate.

**Schema invariants** (enforced at write time by the indexer; verified by `CapabilityGraphSchemaIntegrity()` scorer in §23.8.2):

- Every row in `extensions` has exactly one parent in `meta_skills` (FK constraint).
- Every row in `meta_skills` has exactly one parent in `top_orders` (FK constraint).
- Every row in `entity_edges` has both endpoints existing in either `meta_skills` or `extensions` for the same `snapshot_id` (FK constraint).
- `extensions.effect_class ∈ {read, write, write·hitl, unclassified}` (CHECK constraint).
- `extensions.authority ∈ {high, medium, low}` (CHECK constraint).
- `extensions.lifecycle ∈ {ga, public-preview, beta, deprecated, removed}` (CHECK constraint).
- `extensions.cloud_variance ∈ {invariant, aws-only, azure-only, gcp-only, per-cloud-overlay}` (CHECK constraint).
- `corpus_snapshots.signature` is non-null after promotion (CHECK constraint).

### §23.4.3 Tier C — Vector Search indexes (3)

The retrieval-bearing artifacts. Stage A reads from these; partners' UI search reads from these; the structured tables (Tier B) are queryable by SQL but are not Stage A's primary path.

Specifications in §23.5 below.

### §23.4.4 Atomic promotion + rollback

Promotion is **atomic at the `active_snapshot_id` table level**. The single-row `<bv>.capability_graph.active_snapshot_id` table is written via Delta `MERGE INTO` with the new snapshot's ID; readers always see exactly one active snapshot's worth of data because their queries JOIN against this pointer.

**Rollback semantics**: there is no "rollback" in the sense of un-doing a write. Promotion either succeeds (the new snapshot is active) or never happens (the smoke test failed; the previous snapshot is still active). Manual re-promote-to-N-1 (forcing the previous snapshot to become active again) is supported via the new `brickvision indexer rollback --to <snapshot_id>` CLI command, which writes the historical snapshot's ID into `active_snapshot_id`. Rollback is rate-limited (max 1 per hour per partner, to prevent thrashing); rollbacks emit `CAPABILITY_GRAPH_MANUAL_ROLLBACK` Question for audit.

**30-day previous-snapshot retention**: the `snapshot_retention_job` (separate from the indexer DAG) runs daily and prunes Tier A snapshots older than 30 days, plus their corresponding Tier B rows where `snapshot_id NOT IN (SELECT snapshot_id FROM corpus_snapshots WHERE deactivated_at IS NULL OR deactivated_at >= now - 30 days)`. The 30-day window means rollback to any snapshot promoted in the last 30 days is always available; older snapshots are gone and rollback to them returns `CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION`.

---

## §23.5 Vector Search posture

### §23.5.1 The three indexes

All three are **Mosaic AI Vector Search Delta Sync indexes**, sync mode `TRIGGERED` (post-promotion, by the `upsert_vs_indexes` task — not continuous, because nightly refresh cadence does not need continuous; continuous would burn compute). Sync source is the corresponding Tier B Delta table.

| Index name | Source Delta table | Sync mode | Embedding model | Embedding column | Used by |
|---|---|---|---|---|---|
| `meta_skills_index` | `<bv>.capability_graph.meta_skills` | `TRIGGERED` | `databricks-gte-large-en` | computed from `id || name || description || pattern_tags` | catalog UI search; Stage A meta-skill seed lookup |
| `extensions_index` | `<bv>.capability_graph.extensions` | `TRIGGERED` | `databricks-gte-large-en` | computed from `id || method_signature || synopsis || sample_code_snippet` | Stage A extension/method signature lookup |
| `capability_passages_index` | `<bv>.capability_graph.extensions` (chunked descriptions) UNIONed with chunked blog/docs excerpts | `TRIGGERED` | `databricks-gte-large-en` | computed from chunk text | HippoRAG-2 PPR walks (extends the existing N117 retriever in [`14-context-engineering.md`](./14-context-engineering.md) §11.5.1.A) |

### §23.5.2 Endpoint sharing (D15 default — shared endpoint)

D15 default: a single shared `bv_vs_endpoint` (one Mosaic AI Vector Search endpoint per partner workspace) hosts:

- the 2 existing v0.7.6.1 indexes (`claims_text_index`, `doc_passages_index` from N117)
- the 3 new v0.7.7 capability-graph indexes (`meta_skills_index`, `extensions_index`, `capability_passages_index`)

Total: 5 indexes per endpoint. Mosaic AI VS endpoints support up to 50 indexes per endpoint; we are well under the cap.

**Per-index grants**:

| Index | `bv_indexer_sp` | `bv_app_sp` |
|---|---|---|
| `claims_text_index` | (no access; existing v0.7.6.1 indexer owns this) | READ |
| `doc_passages_index` | (no access; existing v0.7.6.1 indexer owns this) | READ |
| `meta_skills_index` | WRITE | READ |
| `extensions_index` | WRITE | READ |
| `capability_passages_index` | WRITE | READ |

Verified by `VectorSearchEndpointGrants()` install pre-flight. The grant separation preserves isolation despite endpoint sharing.

### §23.5.3 Embedding model + cost

- **Default model**: `databricks-gte-large-en` (Foundation Model API, free under serverless serving). Dimension 1024.
- **Override**: partners can configure a different embedding endpoint via the `BV_INDEXER_EMBEDDING_ENDPOINT` install variable; the indexer's `embed_batch` task reads this variable and routes calls accordingly. Override use cases: partners with customer requirements for self-hosted embeddings, or partners who want to use a higher-quality embedding model.
- **Cost estimate (default)**: at v0.7.7 ship cardinality (~750 extensions + ~54 meta-skills + ~5,000 passage chunks) and 1024-dim embeddings, a cold full refresh embeds ~6,000 entities × ~500 tokens ≈ 3M tokens. At Databricks's documented Foundation Model API serverless pricing (no charge under default), cost is $0; under override to a paid embedding endpoint, cost is per partner's contract (estimated ~$3-15 per cold full refresh; ~$0.30-1.50 per nightly incremental).
- **Caching**: the `<bv>.capability_graph.embedding_cache` table (Tier B, §23.4.2) keys embeddings by content hash; ~90% hit rate on incremental refreshes means only ~10% of entities are re-embedded each night.

### §23.5.4 Degraded fallback to direct SQL

When a VS index sync fails (per §23.3.4 — `CAPABILITY_GRAPH_VS_SYNC_FAILED`), Stage A's `kg_search` falls back to direct SQL queries against the Tier B Delta tables. This fallback path is slower (~10× latency for typical PPR walks because Delta scans replace VS approximate-NN) but functionally complete: the same nodes, edges, and authority weights are accessible.

Fallback is automatic and per-index (if `meta_skills_index` is degraded but `extensions_index` is healthy, Stage A uses VS for extensions and SQL for meta-skills). The Knowledge UI's Corpus tab surfaces each index's health (green / yellow / red) so partners can see when fallback is active.

The degraded path is also the bootstrap path: at install time (before the first indexer refresh has completed), VS indexes don't exist yet; Stage A reads exclusively from Tier B SQL. The first refresh's `upsert_vs_indexes` task transitions the partner from SQL-fallback to VS-primary.

---

## §23.6 Stage A integration + naming migration

### §23.6.1 Stage A retrieval contract change

`stage:agent-design` (was `stage:agent-design`) and `stage:agent-validate` Stage A retrieval pipelines extend their `kg_search` invocations ([`14-context-engineering.md`](./14-context-engineering.md) §11.5.1.A) to walk the capability graph in addition to (not instead of) the partner's workspace KG.

**Pre-v0.7.7 retrieval order**:

1. `kg_search` against `<bv>.kg.subject_cards` + `<bv>.kg.claims_text_index` + `<bv>.kg.doc_passages_index` (workspace KG only)

**v0.7.7 retrieval order** (the v0.7.7 change):

1. `kg_search` against `<bv>.capability_graph.meta_skills_index` + `<bv>.capability_graph.extensions_index` + `<bv>.capability_graph.capability_passages_index` — **the new primary retrieval substrate** (returns the ranked Top-Order / Meta-Skill / Extension seeds matching the user goal)
2. `kg_search` against `<bv>.kg.subject_cards` + `<bv>.kg.claims_text_index` + `<bv>.kg.doc_passages_index` — **the partner KG, now secondary** (returns workspace-specific facts: which catalogs exist, which schemas have which tables, which models are deployed, etc.)
3. PPR seed merge: the contract per [`14-context-engineering.md`](./14-context-engineering.md) §11.5.1.D, extended to merge seeds across both retrieval substrates by source authority weight × match score.

The two substrates are complementary, not competitive: the capability graph tells Stage A *what Databricks can do* and *how to do it*; the workspace KG tells Stage A *what's already in this partner's workspace* and *who has access to it*. Stage A's plan synthesis combines both — e.g., "build a churn classifier" needs `meta:mlflow-tracking/ext:train-with-floor-and-register` (capability graph) AND `<bv>.kg.subject_cards` evidence about which UC tables exist with churn-relevant features (workspace KG).

**Authority arbitration** when both substrates return seeds for the same query: the capability graph's `source_authority` (§23.1.6) determines the authority weight of capability-graph seeds; workspace-KG seeds have a fixed authority weight of 1.0 (workspace evidence is *factual* about the partner's environment, so it never loses to indexed evidence). When the two substrates disagree (e.g., capability graph says "use `meta:ml-lifecycle/ext:promote-to-prod-via-alias`" but workspace KG says "no UC alias `@prod` exists for this model"), Stage A surfaces both as Findings and emits a `Question` of kind `capability_workspace_mismatch` for human review — not silent reconciliation.

### §23.6.2 The `meta:` → `stage:` rename

D13 default — single PR. The rename touches 33 files (per the v0.7.7 design exchange estimate; verified by Phase B's first cascade pass):

- 14 PRD files (every doc that references `meta:agent-*`)
- 6 runtime modules (`brickvision_runtime/meta_skills/agent_*.py` → `brickvision_runtime/stages/agent_*.py`)
- 4 install-step files
- 9 test files (pytest test cases that assert `meta:agent-*` IDs)
- 1 reason-codes catalog (the `meta:agent-*` references in `<bv>.policy.rules` reason-code patterns)

**Backwards compatibility window** (LANDED in v0.7.7 — N174 closure): pre-v0.7.7 audit rows in `<bv>.builds.runs.audit` retain `meta:agent-*` strings as historical record. The v0.7.7 audit replay tooling (`brickvision build --replay`) treats `meta:agent-<id>` and `stage:agent-<id>` as alias-equivalent for read paths only via the helper `brickvision_runtime.core.actor_alias.normalize_actor_prefix()` (called from `brickvision_runtime.core.claim.Claim.from_delta_row` so all read sites benefit transparently) — so an audit row carrying `meta:agent-design` resolves to `stage:agent-design` at replay time. New writes always use `stage:`. The alias-equivalence is removable in a future major version (likely v0.8) once all retained audit data is past its replay-bundle TTL ([`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.3.1).

The `stage:agent-design.has_substage` / `stage:agent-generate.has_substage` etc. relationship spec in [`05-build-pipeline.md`](./05-build-pipeline.md) §7 is renamed throughout to `stage:agent-*.has_substage`; the substages themselves (Sketch, Section, Compose for `agent-design`; the per-file generators for `agent-generate`; etc.) keep their existing IDs since they were never under the `meta:` prefix.

### §23.6.3 Hand-authored exemplar discovery

Stage A's plan synthesis surfaces hand-authored exemplars as **preferred extensions** when an extension's `exemplar_skill_id` field is non-null (per the schema in §23.2.4). The mechanism: when Stage A's `kg_search` returns extension `meta:<m>/ext:<e>` as a seed, and that extension has `exemplar_skill_id: skill:<id>`, Stage A's exemplar-attached prompt template ([`14-context-engineering.md`](./14-context-engineering.md) §11.5.9) loads the hand-authored skill's full SKILL.yaml + DESIGN.yaml + tools.py + skill.py source as context, alongside the indexed extension's typed schema. Stage A's plan thus has both *what to do* (the indexed extension's signature) and *how it has been done with high quality before* (the hand-authored exemplar).

For extensions in Top-Orders 3 and 5 (where no hand-authored exemplars exist at v0.7.7 ship), Stage A receives no exemplar; the plan synthesis is purely from indexed evidence. This is the v0.7.7 acceptable-gap posture per §23.2.6; v0.7.8 closes it via gap-fill.

---

## §23.7 The Knowledge UI page

Per directive 3 of the v0.7.7 design exchange: the partner-side Console exposes a new top-level `/knowledge` route showing — by source section — exactly what was indexed, when, by whom, with what hash, and at what authority weight.

### §23.7.1 Five-tab layout

The new route lives at `apps/console/src/routes/knowledge.tsx`. The existing `/catalog` route from v0.7.6 (which currently shows the static skill catalog from [`11-skill-catalog.md`](./11-skill-catalog.md) §9) is reframed as the "Top-Orders" tab inside the new `/knowledge` route — `/catalog` remains as a legacy URL that 301-redirects to `/knowledge#top-orders`.

| Tab | URL fragment | What partner sees |
|---|---|---|
| **Corpus** (default) | `/knowledge#corpus` | 5 source cards: `databricks-sdk`, `concept-docs (4 clouds)`, `openapi`, `databricks-blog`, `databricks-labs-lakebridge`. Each shows: last refresh ts + hash · entity count · health (green / yellow / red) · last signed-by SP · authority weight · "view raw snapshot" link (downloads from Tier A Volume) |
| **Top-Orders** | `/knowledge#top-orders` | 7 cards (the closed Top-Orders from §23.2.2); click → drills into that Top-Order's meta-skills. Each card shows: meta-skill count · extension count · hand-authored exemplar count · gap badge if 0 exemplars |
| **Meta-Skills** | `/knowledge#meta-skills` | filterable list of all ~54 meta-skills; per row: name · top-order parent · source breakdown badges (e.g., `[sdk: 12]` `[docs: 4]` `[blog: 1]`) · authority badge · last refresh ts |
| **Extensions** | `/knowledge#extensions` | filterable list of ~750+ extensions; per row: id · meta-skill parent · authority badge (`high` / `medium` / `low`) · effect class badge (`read` / `write` / `write·hitl`) · cloud variance badge · sample method signature; click → provenance pane |
| **Refresh history** | `/knowledge#history` | audit-style table of last 30 snapshots: refresh ts · corpus_hash · partial sources · signed-by · promoted (✓ / ✗) · smoke-test pass-rate · refresh duration · embedding cost; click → diff vs prior snapshot |

### §23.7.2 Provenance pane

Right-rail pane (or modal on narrow viewports) shown on any extension click. Shows:

- **Source URL** (clickable; opens external in new tab)
- **File + line number** (if SDK or Lakebridge; clickable opens GitHub at the pinned commit SHA)
- **Commit SHA** (if Lakebridge) or **SDK version** (if SDK)
- **Parsed-at** timestamp + parser version
- **Signed-by** SP (the indexer SP's identity at parse time)
- **Authority score** + which scorer assigned it (`SourceAuthorityAssignment()` per §23.8.2)
- **Linked entities** — clickable graph view; expandable up to 2 hops from the extension's node
- **Cross-cloud note** (if `cloud_variance ≠ invariant`) — shows which clouds have evidence for this extension and whether the partner's `databricks_cloud` matches

### §23.7.3 Seven FastAPI endpoints

In `apps/console-api/src/console_api/routers/knowledge.py` (NEW v0.7.7), all endpoints read-only against the indexer's outputs (the app-SP has READ-ONLY access per §23.5.2):

| Method | Path | Returns |
|---|---|---|
| GET | `/api/knowledge/corpus` | the 5 source cards (per-source hash, ts, counts, health) |
| GET | `/api/knowledge/top-orders` | the 7 Top-Orders with descendant counts (meta-skill count, extension count, exemplar count) |
| GET | `/api/knowledge/meta-skills` | filterable; supports `?top_order=<id>`, `?source=<kind>`, `?authority=<level>` |
| GET | `/api/knowledge/extensions` | filterable; supports `?meta_skill=<id>`, `?source=<kind>`, `?effect_class=<class>`, `?cloud_variance=<class>`, `?lifecycle=<class>` |
| GET | `/api/knowledge/entity/{id}` | full provenance for one entity (top-order / meta-skill / extension); the URL-encoded ID slug accepts `to:`, `meta:`, or `ext:` prefixes |
| GET | `/api/knowledge/snapshots` | last 30 refreshes (audit-style; supports `?limit=<n>` up to 30) |
| GET | `/api/knowledge/snapshot/{id}/diff` | diff vs prior snapshot (added meta-skills, removed extensions, authority changes); supports `?vs=<other_snapshot_id>` for arbitrary pair-wise diff |

All endpoints read from VS indexes for search and Tier B Delta tables for browse — no Job invocation from the UI. **Refresh is Job-triggered only**, on schedule or manual `databricks jobs run-now`. The Console's Refresh-history tab can show "Refresh in progress" status by reading `<bv>.capability_graph.refresh_plan` (the in-flight refresh's row appears here while the DAG is running) but cannot trigger a refresh itself; manual triggers go through the CLI (`brickvision indexer refresh`).

### §23.7.4 Discovery and accessibility

- The `/knowledge` route is added to the top-level navigation in `apps/console/src/components/AppShell.tsx` between `/catalog` (legacy redirect) and `/observability`.
- Accessibility: the existing `VisualBuilderCanvasA11y()` axe-core scorer ([`17-eval-framework.md`](./17-eval-framework.md) §13.3) is extended in v0.7.7 to cover the `/knowledge` route; zero `serious` / `critical` violations gate the v0.7.7 ship.
- Vocabulary discipline: every label / heading / button on the page sources its string from `apps/console/src/strings/ui_strings.yaml` per the v0.7.5 N109 vocabulary policy ([`19-local-development.md`](./19-local-development.md) §15); the new strings (e.g., `knowledge.tab.corpus`, `knowledge.tab.top_orders`, etc.) are added in the v0.7.7 ui_strings.yaml extension and gated by the existing `VocabularyAccessibility()` scorer.
- Performance: the Top-Orders, Meta-Skills, Extensions tabs use TanStack Query with 60s `staleTime` (capability-graph data only changes per refresh, so 60s caching is generous); the Corpus tab uses 5s `staleTime` to surface refresh-in-progress changes faster.

### §23.7.5 Empty-state and bootstrap

Before the first indexer refresh has completed (a window of typically 35-50 minutes after install), the `/knowledge` page shows an empty-state banner: "Capability graph is being built for the first time. Check back in a few minutes." Each tab shows a skeleton placeholder; the Corpus tab's source cards show "Pending first refresh" status. Once the first refresh completes, the page populates.

The Knowledge UI never blocks the rest of the app: partners can still build harnesses during the bootstrap window — Stage A operates in degraded SQL-fallback mode (per §23.5.4) until the indexer's first refresh promotes a snapshot. The empty-state banner's "Check back" CTA also offers a "Build with degraded retrieval" link that explicitly opens a build session with a `degraded_retrieval=true` flag in the build's `BuildPipelineRun` audit row — making the degraded path explicit, not silent.

---

## §23.8 Eval coverage

### §23.8.1 The smoke-test golden set (D17 default)

Locked at v0.7.7 ship; refreshed quarterly via manual review. The legacy fixture lives in `tests/fixtures/capability_graph/smoke_queries_v1.yaml`, but the product contract is now the MLflow/Unity Catalog evaluation dataset defined by `config/evaluation/evalsets.json` and synced by `scripts/sync_mlflow_eval_datasets.py`. The smoke queries are not hardcoded promotion shortcuts; they are curated evaluation records with `inputs`, `expectations`, `source`, and `tags`.

The legacy five-query table remains useful for historical context, but the
operational v0.7.9 contract is the JSONL dataset, not an inline PRD table. The
records in `config/evaluation/capability_graph_retrieval_v1.jsonl` reference
active corpus ids such as docs chunks or extension ids that exist in the current
Capability Graph. Retired fixture-only gold ids must not be used against live
retrieval.

The scorer runner passes the workflow when the manifest gates pass, including
`top_1_hit_rate_floor: 0.80` and expected-context recall when configured.
Runtime scoring is based on recent `rag_search` events joined by query hash, not
hardcoded smoke-test logic in the indexer.

For v0.7.9+, the operational dataset is:

- MLflow dataset: `<BV_CATALOG>.<BV_SCHEMA>.bv_eval_capability_graph_retrieval_v1`
- Manifest: `config/evaluation/evalsets.json`
- Records: `config/evaluation/capability_graph_retrieval_v1.jsonl`
- Workflow: `capability_graph`
- Gate: top-1 hit-rate floor `0.80`, with regression comparison against prior promoted runs.

### §23.8.2 New scorers (NEW v0.7.7)

There are two scorer systems today:

1. **Indexer/runtime substrate scorers** in `src/brickvision_runtime/eval/scorers/`.
   These validate schema integrity, graph linkage, DAG shape, service-principal
   isolation, and other substrate invariants.
2. **Product Evaluation cockpit scorers** in `scripts/run_evaluation_scorers.py`.
   These read `config/evaluation/evalsets.json`, compare curated records to
   recent `evaluation_events`, and persist `scorer_run` events for the
   `/evaluation` page.

The systems are related but not yet unified behind one `mlflow.genai.evaluate`
execution path.

| Scorer | Asserts | Gold set |
|---|---|---|
| `CapabilityGraphSchemaIntegrity()` | every meta-skill has exactly one Top-Order parent; every extension has exactly one meta-skill parent; every entity_edges row has both endpoints existing in same snapshot; CHECK constraints on enums hold | `<bv>.eval.gold_capability_graph_v1` |
| `CapabilityGraphSmokeTestPassRate()` | the 5-query smoke set's hit-rate ≥ 0.95× baseline (the runtime gate); also enforces the bet criterion 13 floor | `tests/fixtures/capability_graph/smoke_queries_v1.yaml` |
| `IndexerDAGTaskSpec()` | static AST check that the rendered Job spec for `bv_capability_indexer` matches the canonical 15-task DAG shape from §23.3.2; every task has correct retries, timeout, compute serverless, and dependency edges | `<bv>.eval.gold_indexer_dag_v1` |
| `BudgetNamespaceIsolation()` | static AST + runtime probe that the indexer's `BudgetGuard` namespace is `indexer` (not `app`) and that no app-side `BudgetGuard` invocation references `indexer` | `<bv>.eval.gold_budget_isolation_v1` |
| `ServicePrincipalIsolation()` | static AST + runtime probe that `bv_indexer_sp` and `bv_app_sp` exist with non-overlapping privilege sets per §23.3.5 | `<bv>.eval.gold_sp_isolation_v1` |
| `VectorSearchEndpointGrants()` | runtime probe of the shared VS endpoint's per-index grants per §23.5.2 | (live probe, no gold set) |
| `SourceAuthorityAssignment()` | every extension has `authority = max(source.weight)` per the source authority table at materialize time | `<bv>.eval.gold_capability_graph_v1` |
| `HandAuthoredSkillExemplarLinkage()` | every hand-authored Layer-0 skill at v0.7.7 ship has a non-null `exemplar_of:` field linking it to a valid `meta:<m>/ext:<e>` ID present in the active snapshot | `<bv>.eval.gold_capability_graph_v1` (the §23.2.6 mapping table) |
| `IndexerRefreshSLO()` | per-tier refresh-duration SLO: nightly cold cache ≤ 50 min p95; nightly incremental ≤ 15 min p95; weekly cold cache ≤ 60 min p95 | `<bv>.capability_graph.corpus_health` 30-day rolling |
| `KnowledgeUIVocabularyCoverage()` | every UI string on the `/knowledge` page sources from `ui_strings.yaml` (extends the existing N110 `VocabularyAccessibility()` scope) | `apps/console/src/strings/ui_strings.yaml` |

10 new scorers; v0.7.7 ship total: 55+ (v0.7.6.9) + 10 = **65+ custom scorers** ([`17-eval-framework.md`](./17-eval-framework.md) §13.3 catalog).

### §23.8.3 Bet criterion 13 (NEW v0.7.7)

Added to [`02-bet-and-principles.md`](./02-bet-and-principles.md) §3 in Phase B cascade:

> **Criterion 13 (NEW v0.7.7).** The Capability Indexer indexes ≥ 90% of the SDK surface (count: methods present in `databricks-sdk==<floor or higher>` that have a corresponding `extensions` row with `authority=high`); AND the 5-query smoke-test golden set's PPR top-1 hit-rate ≥ 0.95 of locked baseline; AND the median nightly-incremental refresh completes in ≤ 15 min p95 over a 30-day rolling window. Falsified by: (a) SDK coverage falling below 90% sustained for 7 consecutive nightly refreshes (indicates indexer rot — alias-table drift, extractor regression, or upstream SDK reshape); (b) smoke-test hit-rate falling below 0.95 baseline for 3 consecutive refreshes (indicates retrieval substrate degradation — embedding model shift or graph-edge weight error); (c) refresh duration p95 exceeding 15 min for 7 consecutive nightly refreshes (indicates throughput regression — usually a docs-corpus crawl size doubling or VS sync slowing).

The criterion is verifiable in production via `<bv>.capability_graph.corpus_health` queries; the GA gate in [`21-roadmap.md`](./21-roadmap.md) §19 includes it among its v0.6 falsifiable conditions.

### §23.8.4 Automatic evaluation events and MLflow lineage (NEW v0.7.9)

Capability Graph evaluation now has two linked records:

1. **Evaluation datasets**: curated MLflow GenAI datasets stored in Unity Catalog. These provide stable regression inputs and expectations.
2. **Evaluation events**: normalized runtime events written whenever BrickVision performs a relevant operation.

The intended indexer contract is an evaluation event at each promotion decision:

- `event_kind = indexer_snapshot`
- `workflow = capability_graph`
- `subject_id = snapshot_id`
- `metrics_json`: smoke hit rate, SDK coverage, source-grounding count, duplicate-key count, refresh duration, sync verification, embedding cost, token counts when available.
- `reason_codes_json`: scorer failures or promotion blockers.
- `mlflow_run_id`: parent MLflow run for the indexer refresh when configured.

This indexer event is planned; the current implemented emitters are the Console
API events for search and ask:

- `event_kind = rag_search` for `/api/knowledge/search`.
- `event_kind = rag_answer` for `/api/knowledge/ask`.
- `workflow = hipporag2_retrieval`.
- Metrics include retrieved chunk count, expanded context count, source count, answer/code length, latency, and error status.

These events allow the standalone Evaluation page to answer both "what is
covered by datasets?" and "what happened in real BrickVision usage?" The
scheduled scorer runner now performs deterministic UC-event scoring by default
and has an opt-in `--mlflow-genai-evaluate` path for retrieval records once
runtime evidence exists. Full trace export into MLflow Agent Evaluation datasets
and UI trend reporting remain follow-on work.

Evaluation writes are not promotion gates by themselves. A promotion gate is a policy decision that reads scorer results. The event table is the audit substrate; MLflow runs/traces are the lineage substrate; dataset records are the regression substrate.

---

## §23.9 New reason codes

All 19 reason codes (15 from §23.1 - §23.5, 2 from §23.7, 2 net new from §23.6) added to the canonical reason-code catalog in [`05-build-pipeline.md`](./05-build-pipeline.md) §7.6 in Phase B cascade. Inventory:

| Code | Source | Severity |
|---|---|---|
| `CAPABILITY_GRAPH_SDK_PARSE_FAILED` | §23.1.1 | hard fail (DAG abort) |
| `CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN` | §23.1.1 | per-method Question (human review) |
| `CAPABILITY_GRAPH_OPENAPI_FETCH_FAILED` | §23.1.2 | soft fail (per-version) |
| `CAPABILITY_GRAPH_OPENAPI_SDK_LINK_MISSING` | §23.1.2 | per-operation Question |
| `CAPABILITY_GRAPH_DOCS_FETCH_FAILED` | §23.1.3 + §23.3.4 | soft fail (per-URL or per-corpus) |
| `CAPABILITY_GRAPH_DOCS_PARSE_FAILED` | §23.1.3 | soft fail (per-page) |
| `CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL` | §23.1.3 | per-corpus Question |
| `CAPABILITY_GRAPH_BLOG_FETCH_FAILED` | §23.1.4 | soft fail (per-URL) |
| `CAPABILITY_GRAPH_BLOG_FILTER_REJECTED_HIGH_VOLUME` | §23.1.4 | Question (allowlist drift; human review) |
| `BLOG_META_SKILL_INFERENCE_FAILED` | §23.1.4 | per-post Question |
| `CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED` | §23.1.5 | hard fail for Lakebridge task; soft fail for snapshot |
| `CAPABILITY_GRAPH_LABS_MODULE_UNKNOWN` | §23.1.5 | per-module Question (human review) |
| `CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED` | §23.3.3 (extract_entities) | hard fail (DAG abort) |
| `CAPABILITY_GRAPH_EMBEDDING_SHARD_FAILED` | §23.3.3 (embed_batch) | soft fail (per-shard) |
| `CAPABILITY_GRAPH_ORPHANED_META_SKILL` | §23.3.3 (build_entity_graph) | hard fail (DAG abort) |
| `CAPABILITY_GRAPH_PERSIST_FAILED` | §23.3.3 (persist_to_uc) | hard fail (DAG abort) |
| `CAPABILITY_GRAPH_VS_SYNC_FAILED` | §23.3.3 (upsert_vs_indexes) | soft fail (per-index; degraded SQL fallback) |
| `CAPABILITY_GRAPH_SMOKE_REGRESSION` | §23.3.3 (smoke_test) | hard fail (no promotion) |
| `CAPABILITY_GRAPH_PROMOTION_FAILED` | §23.3.3 (promote_snapshot) | hard fail (atomic flip didn't apply) |
| `CAPABILITY_GRAPH_TELEMETRY_FAILED` | §23.3.3 (emit_telemetry_and_alerts) | soft fail (snapshot already promoted) |
| `CAPABILITY_GRAPH_REFRESH_OK` | §23.3.3 | success Question (telemetry signal) |
| `CAPABILITY_GRAPH_REFRESH_PARTIAL` | §23.3.3 | partial-success Question |
| `CAPABILITY_GRAPH_REFRESH_FAILED` | §23.3.3 | failure Question |
| `CAPABILITY_GRAPH_REFRESH_SKIPPED_ALL_FRESH` | §23.3.3 (plan_refresh) | benign Question (telemetry signal) |
| `CAPABILITY_GRAPH_MANUAL_ROLLBACK` | §23.4.4 | audit Question (operator-triggered) |
| `CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION` | §23.4.4 | hard fail on rollback request to deleted snapshot |
| `DOCS_SECTION_ALIAS_MISSING` | §23.2.7 | per-section Question (human review) |
| `CAPABILITY_WORKSPACE_MISMATCH` | §23.6.1 | per-build Question (Stage A surfaces; HITL review) |

Total: **28 new reason codes** in the v0.7.7 set.

---

## §23.10 v0.7.7 timeline + N-task allocation

Phase B cascade folds these into [`21-roadmap.md`](./21-roadmap.md) §19.16 (NEW v0.7.7 phase block). Allocations below are estimates; confirmed by tech-lead estimation in Phase B.

### §23.10.1 N-task list (N157 - N188, 32 tasks)

| N | Title | Engineer-weeks | Parallelizable with |
|---|---|---|---|
| N157 | Capability graph schema migration: provision `<bv>.capability_graph.*` UC schema + 13 Delta tables + the Volume + grants + the SHA-256 signature plumbing | 1.0 | N158-N162 (after schema lands) |
| N158 | Source adapter — `sdk_adapter.py` (databricks-sdk Python AST walk; dataclass + service + method enumeration; effect-class heuristic) | 1.5 | N159-N162 |
| N159 | Source adapter — `openapi_adapter.py` (per-version OpenAPI 3.x walk; SDK cross-link via `__databricks_path__`) | 0.7 | N158, N160-N162 |
| N160 | Source adapter — `docs_adapter.py` (sitemap crawl + chunking + per-cloud tagging) | 1.5 | N158, N159, N161, N162 |
| N161 | Source adapter — `blog_adapter.py` (allowlist + LLM scorer + recency-decay metadata) | 1.0 | N158-N160, N162 |
| N162 | Source adapter — `labs_repo_adapter.py` (configured for Lakebridge in v0.7.7; designed shape-compatible for UCX/DQX in v0.7.8) | 1.0 | N158-N161 |
| N163 | Indexer DAG (`run_capability_indexer.py`) — task definitions, dependencies, error handling matrix, retries config | 1.5 | sequential after N157-N162 |
| N164 | `extract_entities` task — kg_extractor structured-output orchestration (extends N113 invoker for the new entity kinds: `top_order_skill`, `meta_skill`, `extension`, `cross_cutting_axis`, `cloud_variance_marker`) | 1.0 | N163 |
| N165 | `embed_batch` task — sharded embedding with content-hash cache | 0.7 | N164 |
| N166 | `build_entity_graph` task — PPR seeds + cross-source linkage + alias-table assignment | 1.0 | N164, N165 |
| N167 | `persist_to_uc` task — `MERGE INTO` writes against snapshot-tagged tables | 0.7 | N166 |
| N168 | `upsert_vs_indexes` task — 3 VS Delta Sync indexes' TRIGGERED sync | 0.7 | N167 |
| N169 | `smoke_test` task — 5-query golden set runner; baseline persistence | 0.5 | N168; first-refresh exception |
| N170 | `promote_snapshot` task — atomic active_snapshot_id flip + SHA-256 content-hash digest write | 0.5 | N169 |
| N171 | `emit_telemetry_and_alerts` task — Question emission + corpus_health update + telemetry event | 0.3 | N170 |
| N172 | Snapshot retention Job — separate serverless Job pruning Tier A + Tier B older than 30 days | 0.5 | parallelizable with N163-N171 (independent) |
| N173 | Stage A retrieval contract change — extend `kg_search` for the dual-substrate walk; PPR seed-merge across capability-graph + workspace-KG (extends [`14-context-engineering.md`](./14-context-engineering.md) §11.5.1.D) | 1.5 | sequential after N167 |
| N174 | `meta:` → `stage:` rename — single PR across 33 files (runtime + 14 PRDs + 9 tests + 4 install steps + 1 reason-code catalog); backwards-compat alias-equivalence in audit replay | 1.5 | parallelizable with N173, N175 |
| N175 | Hand-authored skill `exemplar_of:` field — schema migration in [`04-schemas.md`](./04-schemas.md) §6.1; SKILL.yaml updates for the 15 v0.7.6.4 skills with mappings from §23.2.6 | 0.7 | parallelizable with N173, N174 |
| N176 | Knowledge UI page — 5 tabs in `apps/console/src/routes/knowledge.tsx` + AppShell navigation entry + 301 redirect from /catalog | 1.5 | parallelizable with N177 |
| N177 | Knowledge UI FastAPI endpoints — 7 routes in `apps/console-api/src/console_api/routers/knowledge.py` + `runtime_bridge.py` extensions | 1.0 | parallelizable with N176 |
| N178 | Provenance pane component + 2-hop graph view + cross-cloud note rendering | 1.0 | sequential after N176 |
| N179 | `brickvision indexer` CLI sub-command — `refresh`, `rollback --to`, `status`, `health` | 0.5 | parallelizable with N176-N178 |
| N180 | Install pre-flights — `VectorSearchEndpointGrants()`, `UCSchemaOwnership()`, `BV_INDEXER_*` env vars, indexer SP provisioning | 0.7 | parallelizable with N163-N172 |
| N181 | 10 new scorers (per §23.8.2) | 1.5 | parallelizable with rest |
| N182 | 28 new reason codes catalog updates + Question emission tests | 0.3 | parallelizable with N181 |
| N183 | Eval gold sets — `<bv>.eval.gold_capability_graph_v1`, `<bv>.eval.gold_indexer_dag_v1`, `<bv>.eval.gold_budget_isolation_v1`, `<bv>.eval.gold_sp_isolation_v1` | 0.7 | parallelizable with N181 |
| N184 | Bet criterion 13 in [`02-bet-and-principles.md`](./02-bet-and-principles.md) §3 + GA-gate query updates in `pre_flight.ga_gate.*` | 0.3 | parallelizable with N181-N183 |
| N185 | `.env` extensions: `BV_INDEXER_SCHEDULE_CRON`, `BV_INDEXER_SCHEDULE_DISABLED`, `BV_INDEXER_FRESHNESS_TOLERANCE_DAYS`, `BV_INDEXER_EMBEDDING_ENDPOINT`, `BV_BUDGET_NAMESPACE` (and runtime plumbing) | 0.5 | parallelizable with rest |
| N186 | Phase B cascade — edits to 12 PRDs (02-bet, 05-build-pipeline, 11-skill-catalog, 12-visual-builder, 14-context-engineering, 16-identity-audit-replay, 17-eval-framework, 18-architecture, 19-local-development, 21-roadmap, 22-changelog, README) | 1.5 | sequential after this lead doc lands |
| N187 | Self-bootstrap test extension — verify the indexer's outputs are consumed by the existing `stage:agent-design` self-bootstrap from [`09-self-bootstrap.md`](./09-self-bootstrap.md) §7.10; the round-trip should still produce byte-identical generated code, now grounded in the capability graph | 0.7 | sequential after N173-N174 |
| N188 | v0.7.7 release notes + GA-gate re-run + manual smoke test of all 5 golden queries against a partner-installable snapshot | 0.5 | sequential at end |

**Total**: 24.0 engineer-weeks across 32 tasks, with substantial parallelism (peak parallelism: 6 tasks during N158-N162 + N172 + N180-N183 + N185 phase). Single-team duration: ~10-12 weeks. Two-team duration: ~6-8 weeks.

### §23.10.2 Phase -1 prerequisites

Two new spike tasks added to [`21-roadmap.md`](./21-roadmap.md) §19.0:

- **N0-12.15 (NEW v0.7.7)**: Capability-graph empirical sizing spike (~1 week). Validates the empirical numbers in §23.1 and §23.2 against the actual `databricks-sdk==<floor>` + sitemap snapshots at v0.7.7-feature-freeze time. If counts deviate > 20% from the §23.1 / §23.2 numbers (e.g., SDK has grown to 1,000+ methods, or docs sitemap has shrunk significantly), the spike outputs an updated estimate and the N163 - N188 task estimates are re-baselined. Gating on accuracy of capacity planning.

- **N0-12.16 (NEW v0.7.7)**: 5-query smoke baseline establishment (~0.5 week). Runs the 5 golden queries against a manually curated reference snapshot to establish the v1 baseline that subsequent refreshes compare against. Without this baseline, the smoke test's first-refresh exception path is the only possible behaviour, which means no regression detection until the second refresh. Gating because regression detection from refresh #2 onwards is a real-quality requirement.

Both are gating tasks: v0.7.7 ship cannot proceed until both pass. Total Phase -1 increment: 1.5 weeks.

### §23.10.3 Timeline impact

- Single-team v0.6 timeline: 17-25 mo (v0.7.6.9 baseline) + 10-12 weeks (v0.7.7 work) = **18-28 mo**
- Two-team v0.6 timeline: 13-16 mo (v0.7.6.9 baseline) + 6-8 weeks (v0.7.7 work) = **14-18 mo**

The increment is significant (~12-18% timeline expansion) and is justified by:

1. The bet criterion 13 GA gate, which makes capability-graph health a *falsifiable* v0.6 success condition (not a v0.7+ deferral).
2. The catalog-page-fabrication issue raised by the v0.7.7 design exchange — the existing static catalog is empirically wrong (shows 2 fake skills instead of 15 real ones); shipping v0.6 with that UX would damage SI partner trust and likely require an emergency v0.7.7.1 anyway, at higher cost.
3. The directive-2 expansion (Microsoft docs + blog + Lakebridge) materially closes Top-Order 3 and Top-Order 5 gaps; without it, v0.6 ships with two empty Top-Orders that visibly under-serve the Migration & Modelling vertical, which is a primary SI-partner use case (Lumen, DNB, Frontier, GCI Alaska, all named-customer deployments per the v0.7.6.8 confidence-establishment carryforward).

The v0.6 GA gate retains GO posture at 91% confidence (v0.7.6.9 baseline) per [`21-roadmap.md`](./21-roadmap.md) §19; no confidence regression from this expansion because the empirical SDK + sitemap probes done in the v0.7.7 design exchange validated the capability graph's buildability against real Databricks surfaces.

---

## §23.11 Budget + rate-limit posture

| Concern | Posture |
|---|---|
| LLM budget for indexer | Separate `BudgetGuard` namespace `indexer.*` per §23.3.5; default per-day cap 10M tokens (sufficient for full nightly refresh × 30 days at ~3M tokens / refresh + buffer); partner-tunable via `BV_INDEXER_DAILY_TOKEN_CAP` env var |
| Embedding budget for indexer | Default `databricks-gte-large-en` is free under serverless serving; if `BV_INDEXER_EMBEDDING_ENDPOINT` is overridden to a paid endpoint, budget falls under `indexer.embedding` sub-namespace with default per-day cap $50; partner-tunable via `BV_INDEXER_DAILY_EMBEDDING_BUDGET_USD` |
| Rate limits on docs.databricks.com / learn.microsoft.com | Default 4 req/s per corpus task; respects `Retry-After` 429 responses; backs off exponentially on transient 5xx; persistent 429 over 5 min auto-disables the corpus for the rest of the refresh and emits `CAPABILITY_GRAPH_DOCS_FETCH_FAILED` |
| Rate limits on databricks.com/blog | 2 req/s (lower than docs because the blog is more aggressively cached at CDN; 4 req/s consistently triggers 429) |
| Rate limits on github.com / pypi.org | Single clone + single pip install per refresh; rate limits not load-bearing |
| Stage A budget | Unchanged from v0.7.6.9 (the `app.*` namespace per [`13-model-routing-and-budget.md`](./13-model-routing-and-budget.md) §11.4); indexer cannot bleed into this namespace per §23.3.5 |
| Knowledge UI request budget | Read-only against Tier B + VS; no LLM calls; unbounded — partner ops can browse freely without budget impact |

---

## §23.12 What we do NOT do (scope discipline)

Per [`01-overview.md`](./01-overview.md) §0 / [`05-build-pipeline.md`](./05-build-pipeline.md) §7.4.1 conventions, surface what's *not* in v0.7.7 alongside what is:

- **No multi-cloud Stage A retrieval.** Stage A reads the partner's installed-cloud docs corpus + cloud-invariant SDK + Lakebridge + blog. The other 3 cloud-specific docs corpora are indexed and present in Tier B / Tier C, but Stage A's default retrieval filter excludes them (cloud-mismatch weight 0.3). Partners running multi-cloud workloads explicitly opt in via the `axis_filters: {cloud_variance: "any"}` parameter from §23.2.5. The "auto-detect cross-cloud queries" behaviour is a v0.7.8 backlog item (`v0.7.8-auto-cross-cloud-detection`).

- **No multi-region docs corpora.** v0.7.7 indexes only the `en` (English) docs sub-tree per cloud; non-English corpora (`docs.databricks.com/aws/ja/`, `docs.databricks.com/aws/zh-CN/`, etc.) are not indexed. Partners requiring non-English Stage A retrieval (rare; most SI partner customers run English UC contents) are deferred to v0.8.

- **No real-time indexing.** Refresh cadence is daily (D16 default). Partners requiring near-real-time visibility of newly-published Databricks features (e.g., a Public Preview announcement on Tuesday and a build needing it on Wednesday) can fire on-demand refreshes, but the indexer does not stream new docs as they are published. CDN-edge near-real-time is a v0.8 architectural change (would require a different adapter shape).

- **No cross-partner shared cache.** Each partner workspace has its own snapshot, its own VS endpoint, its own embeddings. Centrally hosted "BrickVision-managed snapshots" would simplify cost but break the air-gap / data-residency requirements that are load-bearing for SI engagements ([`19-local-development.md`](./19-local-development.md) §15).

- **No automatic Top-Order taxonomy expansion.** The 7 Top-Orders are closed at v0.7.7 ship. Adding an 8th is a major-version PRD change (`top_orders.schema_version` bump + manual review of every meta-skill's parent reassignment). The closed-set discipline is intentional; an LLM-driven open-set Top-Order would drift across refreshes and break the partner-facing "what does Databricks do?" mental model.

- **No automatic UCX / DQX / lsql / blueprint / mosaic indexing.** D10 default — Lakebridge alone in v0.7.7. The `LabsRepoAdapter` is shape-compatible for these in v0.7.8 (config-only, no new code).

- **No automatic Microsoft Tech Community indexing.** D11 default — blog corpus only covers `databricks.com/blog`. Microsoft Tech Community Azure Databricks-tagged posts are deferred to v0.7.8.

- **No partner-tunable source authority weights.** §23.1.6's authority order is system-level configuration, not partner-tunable. Partners changing relative authority would shift Stage A retrieval semantics in ways that break replay determinism across the partner population. Partner-level overrides are a v0.8 governance question (likely "no" — the closed-set discipline is the better default).

- **No write-side capability indexing for non-Databricks platforms.** v0.7.7 indexes Databricks + Databricks Labs only. Indexing Snowflake / Redshift / BigQuery for cross-platform suggestion (e.g., "show me the Databricks equivalent of Snowflake's Snowpark UDF") is a v0.8+ exploration; the v0.7.7 substrate would need a major shape change to express cross-platform equivalence.

- **No retrieval against Databricks customer engagements.** v0.7.7 indexes only public / partner-installed-package sources. BrickVision does not (and never will) index a partner's customers' workspaces — that would violate the data-residency contract that's the foundation of partner SI trust.

---

## §23.13 Carry-forward decisions table (D4-D17 final state)

Decisions accumulated across the v0.7.7 design exchange. State at this lead doc landing:

| D# | Decision | State | Rationale |
|---|---|---|---|
| D4 | 7 closed top-order categories | LOCKED | Empirical SDK module coverage check (§23.2.2) confirms 7 covers all 21 SDK sub-modules with no gaps |
| D5 | Per-cloud corpora; partner picks cloud at install; indexer handles by cloud | LOCKED | Directive 2 confirms Microsoft Learn is in scope (4 docs corpora total) |
| D6 | Indexer Job runs in partner workspace (not centrally hosted) | LOCKED | Air-gap / data-residency requirement |
| D7 | `docs.lookup` becomes a thin wrapper over `capability_passages_index` PPR walk | LOCKED | Side effect of the capability graph design |
| D8 | Cross-cloud build invariance: build outputs cloud-tagged; same-skill-different-cloud builds produce 2 distinct DAG manifests | DEFAULT TAKEN | Recommended in v0.7.7 design exchange |
| D9 | Top-order ID style: `to:data-engineering` lowercase-hyphen | DEFAULT TAKEN | Recommended in v0.7.7 design exchange |
| D10 | UCX/DQX/lsql/blueprint/mosaic deferred to v0.7.8 | DEFAULT TAKEN | Lakebridge alone fills directive-2 gap; `LabsRepoAdapter` shape-compatible |
| D11 | Microsoft Tech Community blogs deferred to v0.7.8 | DEFAULT TAKEN | MS Learn covers Azure Databricks API; tech community is community Q&A |
| D12 | Blog filtering: allowlist + LLM scorer | DEFAULT TAKEN | ~25% skill-bearing keep rate empirically; allowlist alone too coarse, LLM alone too costly |
| D13 | `meta:` → `stage:` rename: single PR across 33 files | DEFAULT TAKEN | Mechanical rename; partial state worse than full state |
| D14 | Blog recency decay: 365-day half-life | DEFAULT TAKEN | Tune in v0.7.8 from Stage A retrieval scorer outcomes |
| D15 | Shared `bv_vs_endpoint`; indexer-SP WRITE on capability indexes; app-SP READ-ONLY | LOCKED | Cost + networking simplicity; SP-level grant separation preserves isolation |
| D16 | Refresh cadence: nightly + on-demand (configurable per partner via 3 tiers) | LOCKED | Most partners want fresh-by-morning; 3 tiers covers the change-control + cost-optimised cohorts |
| D17 | 5-query golden smoke set; locked at v0.7.7 ship; refreshed quarterly | LOCKED | First-refresh exception path; quarterly review limits drift |

LOCKED = decision is final and not partner-tunable. DEFAULT TAKEN = decision uses the recommended default; pushback during cascade or implementation can revisit. All 14 decisions are recorded; future PRD changes trace to a specific D# for traceability.

---

## §23.14 Open questions (parking lot)

Carried forward to v0.7.8+ unless re-prioritized:

1. **Cross-cloud auto-detect.** Stage A heuristics for noticing a query implicitly references multi-cloud (e.g., "migrate from AWS Databricks to Azure Databricks") and auto-relaxing the cloud-variance filter. Currently partner-explicit via `axis_filters` in §23.2.5; v0.7.8 candidate.

2. **UCX wire-up.** UCX (`databrickslabs/ucx`) is the obvious next labs-package for `LabsRepoAdapter`. Adds ~30 meta-skill / extension entries under `to:migration-ingestion` and `to:governance-finops` (UC migration helper, grants migration). v0.7.8 candidate.

3. **DQX wire-up.** DQX (`databrickslabs/dqx`) is the data-quality-framework labs package. Adds ~15 meta-skill / extension entries primarily under `to:data-engineering-design`. v0.7.8 candidate.

4. **Pattern-extension surfacing in the Knowledge UI.** Pattern-extensions (no SDK method backing; e.g., `meta:dbt-on-databricks/ext:dbt-orchestrated-by-lakeflow`) currently render with a generic "Pattern" badge. A richer rendering — copyable code snippets, embedded Lakeview-style mini-dashboard for each pattern, partner-filterable by SI vertical — is a v0.7.8 UX enhancement.

5. **`docs.lookup` deprecation.** With `capability_passages_index` covering the docs surface, `skill:docs.lookup` is structurally redundant — its only behaviour is now to delegate to the index. v0.8 candidate for removal; v0.7.7 retains it for back-compat.

6. **Per-partner-engagement source authority overrides.** Some SI partners may want to weight blog evidence higher (or lower) for their specific customer engagements. v0.8 governance question; explicit non-goal at v0.7.7 (closed-set discipline preserved).

7. **Indexer job parallelization across workspaces.** SI partners running multiple BrickVision installs (one per customer workspace) currently run N independent indexer Jobs. A federated indexer (one master Job that fans out to N workspaces) would reduce cost; complex networking + cross-workspace SP delegation; v0.8+ exploration.

8. **Embedding model fine-tuning.** Partners with specialized domain vocabularies (e.g., medical, legal, financial) might benefit from fine-tuned embeddings for the capability graph. Requires partner-specific training data; v0.8+ governance + cost question.

9. **Temporal capability graph queries.** Stage A queries against historical snapshots ("what could Databricks do 6 months ago?") — currently supported via `WHERE snapshot_id = <historical>` in Tier B SQL but not surfaced in the Knowledge UI. v0.7.8 UX enhancement candidate.

10. **GA gate sub-criterion: cloud variance coverage.** Currently bet criterion 13 has no cloud-variance sub-criterion. A future addition: ≥ 70% of cloud-variant extensions have evidence from all 3 Databricks docs corpora (not just the partner's installed cloud). v0.7.8 falsifiability tightening.

These all explicitly *not* gating v0.6; they are carryover for the post-v0.6 roadmap.

---
