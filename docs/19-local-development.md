# 19 · Local development

**Covers:** §15.1 (`.env` model — REVISED v0.7.6.4: removes `BV_ML_REPLAY_TOLERANCE_PCT` in favour of the `<bv>.config.ml_replay_tolerance_defaults` Delta table; adds 4 new vars for LLM determinism, Lakeflow dry-run timeout, UC bindings cache TTL, Lark grammar override toggle), §15.2 (local dev modes), §15.3 (loader precedence), §15.4 (generated harnesses ship `.env.example` too), §15.5 (install runbook — REVISED v0.7.6.4: 2 new pre-flights `pre_flight.write_target_catalog_bound_rw` + `pre_flight.lakeflow_pipeline_create_quota`; 2 new post-deploy steps `post_deploy.write_production_aliases` + `post_deploy.write_ml_replay_tolerance_defaults`; renamed cross-workspace constitutional rule. **REVISED v0.7.6.5**: 3 new SDK/runtime floor pre-flights `pre_flight.databricks_sdk_version` (≥ 0.68.0 — the Lakehouse Monitoring + Workspace Bindings floor) + `pre_flight.python_version` (≥ 3.10 — the UC Functions for tools floor) + `pre_flight.serverless_env_version` (Serverless env client="2"); 1 new post-deploy step `post_deploy.start_serving_alias_drift_watcher` for the v0.7.6.5 alias-drift watcher Job that closes the buildability gap that Databricks Model Serving's `served_entities[]` does not auto-track alias re-points), §15.6 (the `brickvision` CLI)
**Related:** [`13-model-routing-and-budget.md`](./13-model-routing-and-budget.md) §11.1 + §11.6 (the `BV_MODEL_ROLE_*` overrides + the LLM determinism contract), [`08-transpiler.md`](./08-transpiler.md), [`04-schemas.md`](./04-schemas.md) §6.4 + §6.5 + §6.5.4.1 + §6.5.7, [`17-eval-framework.md`](./17-eval-framework.md) §13.5, [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.4 invariant 8 + §12.3.1 + §12.5.2 (the v0.7.6.4 UC-bindings delegation), [`11-skill-catalog.md`](./11-skill-catalog.md) §9.1.5 (the new `skill:ml.assign-alias`)
**Audience:** Engineers cloning the repo for the first time; CI authors; partners doing local iteration; operators running `brickvision build --resume` after coordinator process death.
**Status:** v0.7.8 (operational) — local dev flow fully working: `start_local_spa.sh` launches Vite + FastAPI, reads from Lakebase Autoscaling (Synced Tables) and Mosaic AI Vector Search. The `/knowledge` route displays all 5 tabs with live data, semantic search, multi-hop provenance graph, and grounded code generation. **NEW v0.7.8 §15.1** adds `BV_KG_EXTRACTOR_ENDPOINT=databricks-claude-haiku-4-5`, `BV_INDEXER_DISABLE_BLOG=true`, `BV_LAKEBASE_PROJECT_ID=brickvision`, `BV_LAKEBASE_BRANCH=main`. **NEW v0.7.7 §15.1** adds 7 new env vars under the v0.7.7 Capability Indexer block: `BV_INDEXER_SCHEDULE_CRON` (default `0 2 * * *` per D16 default in [`23-databricks-capability-graph.md`](./23-databricks-capability-graph.md) §23.3.6), `BV_INDEXER_SCHEDULE_DISABLED`, `BV_INDEXER_FRESHNESS_TOLERANCE_DAYS` (default 1; partner-relaxable to 2 per cut-line 13c), `BV_INDEXER_EMBEDDING_ENDPOINT` (default `databricks-gte-large-en`), `BV_INDEXER_DAILY_TOKEN_CAP` (default 10M), `BV_INDEXER_DAILY_EMBEDDING_BUDGET_USD` (default $50), `BV_BUDGET_NAMESPACE` (defaults `app`; set to `indexer` inside the indexer Job's task code). **NEW v0.7.7 §15.5** adds 4 new pre-flights (`pre_flight.indexer_sp_provisioned`, `pre_flight.indexer_budget_namespace_isolated`, `pre_flight.uc_schema_capability_graph_ownership`, `pre_flight.vector_search_endpoint_grants`) + 2 new post-deploy steps (`post_deploy.start_capability_indexer`, `post_deploy.start_capability_graph_retention`). **NEW v0.7.7 §15.6.6** adds the `brickvision indexer <refresh|rollback|status|health>` CLI sub-command for operator-driven indexer ops. The deploy-time pre-flights (`HarnessTargetCatalogBindings()` and `LakehouseMonitoringEmbedEnabled()`) are stack-agnostic and unchanged. v0.7.6.9 carry-over: install runbook gains a new pre-flight family for the React stack: `pre_flight.visual_builder_assets` verifies `apps/console/dist/index.html` exists + the chunked JS files are SHA-pinned in `apps/console/dist/.bundle-sha.json`; `pre_flight.visual_builder_typecheck` runs `tsc --noEmit` against `apps/console/` + `apps/console-api/` and fails on any type error; `pre_flight.visual_builder_lockfile_clean` runs `pnpm install --frozen-lockfile` and fails on any drift. Failures emit `VISUAL_BUILDER_ASSETS_MISSING` (unchanged from v0.7.6.1) or new typecheck / lockfile reason codes. The deploy-time pre-flights (`HarnessTargetCatalogBindings()` and `LakehouseMonitoringEmbedEnabled()`) are stack-agnostic and unchanged. v0.7.6.8 carry-over: deep-research pass — install runbook unchanged. NEW v0.7.6.8 deploy-time pre-flight `LakehouseMonitoringEmbedEnabled()` (per [`17-eval-framework.md`](./17-eval-framework.md) §13.3) joins `HarnessTargetCatalogBindings()` in the deploy-time pre-flight set: asserts (a) workspace AI/BI Dashboard embedding is enabled, (b) `databricksapps.com` is in the workspace dashboard embedding allow-list, (c) per-output-table dashboard discovery + harness-SP `BROWSE` grants. Missing → emits `LAKEHOUSE_MONITORING_EMBED_DISABLED` with the pre-filled `databricks workspace-conf set ...` CLI command for self-remediation. Customer-side workspace admin pre-condition added to the partner onboarding runbook (one-time per workspace). Customer-ops Databricks-fluency assumption empirically reconfirmed for primary Telco vertical via 4 named-customer Databricks deployments (Lumen, DNB, Frontier, GCI Alaska — see [`22-changelog.md`](./22-changelog.md) v0.7.6.8 entry research axis 4); no spec change to the install runbook. Carry-over from v0.7.6.7: install runbook unchanged; the v0.7.6.7 pre-flight is at **deploy time**, not install time — `HarnessTargetCatalogBindings()` runs in `meta:agent-deploy` per [`17-eval-framework.md`](./17-eval-framework.md) §13.3 and verifies (a) the harness's `target.catalog` exists with READ_WRITE Workspace-Catalog Binding to the executing workspace; (b) the harness's SP can create the four `<customer>.<harness_name>.{audit, runs, cost, kg}` schemas; (c) the customer ops UC group exists with SELECT on those schemas; (d) every customer user listed in `manifest.support.hitl_approvers` has EXECUTE on every `manifest.skills[].uc_function`. Missing state aborts deploy with one of `WORKSPACE_CATALOG_BINDING_MISSING`, `HARNESS_SP_PRIVILEGE_INSUFFICIENT`, `CUSTOMER_OPS_GROUP_MISSING`, `CUSTOMER_USER_INSUFFICIENT_PRIVILEGE` per [`05-build-pipeline.md`](./05-build-pipeline.md) §7.6 — never produces a half-installed harness. Install pre-flight set carried forward from v0.7.6.6 unchanged. v0.7.6.6 carry-over: 2 new install pre-flights — `pre_flight.routing_table_endpoint_retirement_check` (closes the deep-research finding that the Databricks FMS retirement window — 3 months pay-per-token, 6 months provisioned throughput — will absorb at least one model-generation transition during the 17-25 mo build window; e.g., Llama 3.3 70B → Llama 4 Maverick once Maverick is GA on pay-per-token; new reason code `ENDPOINT_RETIREMENT_WITHIN_PROJECT_HORIZON` per [`05-build-pipeline.md`](./05-build-pipeline.md) §7.6) and `pre_flight.mlflow_responses_tracing_capability` (closes the upstream [mlflow/mlflow#22598](https://github.com/mlflow/mlflow/issues/22598) Responses-API autolog gap; new reason code `MLFLOW_RESPONSES_TRACING_INCOMPLETE` and revised `MLflowAgentsLayering()` scorer per [`17-eval-framework.md`](./17-eval-framework.md) §13.3 and [`06-design-pipeline.md`](./06-design-pipeline.md) §7.7.A). Adds new env var `BV_INSTALL_RETIREMENT_HORIZON_DAYS` (default 180) and new Delta config table `<bv>.config.fms_retirement_calendar` refreshed by a new serverless Job `brickvision_runtime/install/fms_retirement_calendar_refresher.py`. Adds new MLflow compatibility shim `brickvision_runtime/mlflow_compat.py` for the MlflowOpenAgentTracingProcessor pinning + duplicate-span filter.)

---

## §15.1 `.env` model

`.env.example` is committed. Engineers copy to `.env` (git-ignored) and fill in.

```ini
# --- Databricks workspace connection ---
DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
DATABRICKS_TOKEN=<PAT>                              # never commit
DATABRICKS_WAREHOUSE_ID=<warehouse_id>              # serverless SQL warehouse — REQUIRED for all SQL execution
                                                     # NOTE v0.7.6.2: DATABRICKS_CLUSTER_ID intentionally absent.
                                                     # All BrickVision compute is Databricks Serverless per discipline
                                                     # rule 12 (01-overview.md §0): SQL → serverless warehouse,
                                                     # batch → serverless Jobs (sutra-style spark_python_task +
                                                     # environments[].spec — see scripts/launch.py), Apps → serverless
                                                     # Apps, model serving → Mosaic AI serverless serving. No
                                                     # all-purpose or job clusters anywhere.

# --- v0.7.9 Config bundle pointers (see 24-agent-operating-model.md §24.4-§24.5) ---
# .env bootstraps BrickVision itself. It does NOT carry partner/customer workspace
# connection profiles or partner skill definitions. Those live in YAML bundles
# with secret references and content hashes for replay.
BV_CONFIG_DIR=config
BV_ACTIVE_WORKSPACE_PROFILE=partner-dev
BV_SKILL_PACK_MANIFEST=config/skill-packs.yaml

# --- Model endpoints ---
# Local/dev intentionally use only two endpoint classes to avoid routing drift.
LLM_GENERAL_TASKS=databricks-qwen3-next-80b-a3b-instruct
LLM_EMBEDDING_TASKS=databricks-qwen3-embedding-0-6b

# --- HippoRAG-2-style retrieval (NEW v0.7.6 + revised v0.7.6.1 — see 14-context-engineering.md §11.5.1) ---
BV_KG_SEARCH_MAX_SUBGRAPH_EDGES=50000                # ceiling for in-process NetworkX PPR; KG_PPR_SUBGRAPH_TOO_LARGE on miss; raise up to 250000 per §11.5.1.C option (b)
BV_KG_INDEX_MAX_LAG_SEC=600                          # both maintainers' lag tolerance; KG_INDEX_LAG_EXCEEDED + degraded panel on miss
BV_DOCS_LOOKUP_FRESHNESS_DEFAULT_SEC=86400           # default freshness window for skill:docs.lookup cache hits
BV_KG_SEARCH_ENABLED=true                            # NEW v0.7.6.1 — set false (or auto-set by Phase -1 N0-12.10 cut-line) to ship v0.6.0 with kg_search defaulted OFF; per-skill kg_search calls then return KG_SEARCH_DISABLED_BY_GATE

# --- State-management + replay (NEW v0.7.6.3 — see 16-identity-audit-replay.md §12.4 invariants 8-10) ---
BV_REPLAY_MAX_AGE_DAYS=90                            # max replay-back age; pre_flight.replay_retention_consistent
                                                     # asserts every replayable Delta table has
                                                     # delta.deletedFileRetentionDuration AND
                                                     # delta.logRetentionDuration >= this value
BV_BUILD_LIVENESS_TIMEOUT_SEC=600                    # orphan watcher Job marks build paused_orphaned if
                                                     # now() - last_audit_event_at > this threshold;
                                                     # serverless watcher cron runs every 5 min
BV_HITL_WATCHER_INTERVAL_SEC=60                      # HITL watcher Job poll cadence; below this is over-polling
BV_TRACE_TIME=true                                   # whether brickvision_runtime.core.time records every now()
                                                     # call as a Claim under build:<id>:time:<seq>; auto-set
                                                     # to true in {local, dev, stg} and false in prod (cost)
# BV_ML_REPLAY_TOLERANCE_PCT — REMOVED v0.7.6.4 (was 1.0 in v0.7.6.3). Replaced with the per-metric
# tolerance contract in <bv>.config.ml_replay_tolerance_defaults Delta table (see 04-schemas.md §6.5.7)
# overridable per-skill via MLTrainingSpec.replay_tolerance. Single global tolerance was a P7 violation
# because metric-class tolerances differ fundamentally (AUC absolute vs accuracy relative vs RMSE relative).

# --- v0.7.6.4 LLM determinism + Lakeflow + UC bindings ---
BV_LLM_DETERMINISTIC_DECODING=true                   # if true (the only legal value in build pipeline),
                                                     # brickvision_runtime/llm/deterministic_call.py fixes
                                                     # temperature=0/top_k=1/top_p=1.0 + asserts response_format
                                                     # is non-null with strict:true or syntax:"lark"; CI lint
                                                     # also rejects direct openai.OpenAI() in src/brickvision*/**
                                                     # (per 13-model-routing-and-budget.md §11.6 + discipline rule 14)
BV_LAKEFLOW_DRY_RUN_TIMEOUT_SEC=120                  # Lakeflow create-pipeline dry-run timeout for the
                                                     # WriteSideStaticValidation path; longer than typical
                                                     # to absorb cold-start; surfaces LAKEFLOW_DRY_RUN_TIMEOUT
                                                     # on miss (which is treated as LAKEFLOW_DRY_RUN_FAILED)
BV_UC_BINDINGS_PRE_FLIGHT_CACHE_TTL_SEC=300          # how long the UC workspace-bindings pre-flight check
                                                     # caches the binding-mode result per (catalog, workspace_id);
                                                     # 5 minutes balances freshness against API call volume
BV_LARK_GRAMMAR_OVERRIDE_ENABLED=false               # set true to opt the partner's pyspark_codegen / sql_codegen
                                                     # roles into GPT-5-Lark-grammar custom tools when provisioned
                                                     # throughput is available; default false uses the Llama 3.3
                                                     # 70B pay-per-token-tier fallback per the v0.7.6.4 honest-take
                                                     # in 13-model-routing-and-budget.md §11.1

# --- v0.7.7 Capability indexer (NEW — see 23-databricks-capability-graph.md §23.3) ---
BV_INDEXER_SCHEDULE_CRON=0 2 * * *                   # nightly at 02:00 UTC (D16 default); Databricks Job
                                                     # quartz cron format. Partner-tunable to weekly
                                                     # ("0 2 * * 0") or on-demand-only (set BV_INDEXER_SCHEDULE_DISABLED=true).
BV_INDEXER_SCHEDULE_DISABLED=false                   # if true, the bv_capability_indexer Job is unscheduled
                                                     # (reachable only via brickvision indexer refresh CLI);
                                                     # use for partner-controlled refresh cadences or when
                                                     # the partner's policy disallows nightly automated jobs.
BV_INDEXER_FRESHNESS_TOLERANCE_DAYS=1                # the active snapshot's max age before
                                                     # CAPABILITY_GRAPH_SNAPSHOT_STALE Question fires;
                                                     # partner-relaxable to 2 (cut-line 13c) per
                                                     # 02-bet-and-principles.md §3 criterion 13.
LLM_EMBEDDING_TASKS=databricks-qwen3-embedding-0-6b  # the Mosaic AI Foundation Model embedding endpoint
                                                     # the indexer's embed_batch task uses.
BV_INDEXER_DAILY_TOKEN_CAP=10000000                  # daily token cap for the indexer's kg_extractor calls;
                                                     # exceeded → CAPABILITY_GRAPH_TOKEN_CAP_EXCEEDED Question + abort.
BV_INDEXER_DAILY_EMBEDDING_BUDGET_USD=50             # advisory daily USD budget for embedding endpoint usage;
                                                     # reported in <bv>.capability_graph.corpus_health.embedding_cost_usd_30d
                                                     # (advisory, not enforced; emits Question at 80%).
BV_BUDGET_NAMESPACE=app                              # the BudgetGuard namespace for the running process;
                                                     # defaults to "app" (partner-side console + harness use this);
                                                     # set to "indexer" inside the bv_capability_indexer Job's
                                                     # task code so the indexer's spend is tracked separately
                                                     # from the app's spend per BudgetNamespaceIsolation() scorer
                                                     # (17-eval-framework.md §13.3 + 23-databricks-capability-graph.md §23.3.5).

# --- BrickVision install scope (1 catalog + 1 schema) ---
# Databricks UC is 3-level (catalog.schema.table). Per v0.7.7 schema
# consolidation every BrickVision UC object lives at
# <BV_CATALOG>.<BV_SCHEMA>.<table> — a single flat schema. The previous
# per-domain schemas (config / capability_graph / staging / kg / audit /
# cache / builds / policy / sessions / eval) were collapsed into one;
# table names alone identify each object globally (they were already
# unique across the 28 tables).
#
# Why one schema:
#   - one OWNER, one set of grants, one DROP SCHEMA CASCADE on uninstall;
#   - simpler routing layer (no per-call schema decision);
#   - the V/S indexes + the indexer staging Volume live alongside Delta
#     tables under the same schema.
#
# The single UC Volume that the capability indexer stages inter-task
# JSON in is <BV_CATALOG>.<BV_SCHEMA>.<BV_INDEXER_STATE_VOLUME>
# (default volume name: indexer-state; full path:
#  /Volumes/<catalog>/<schema>/<state_volume>/runs/<run_id>/).
# This is NOT a state store — every typed capability-graph row lives in
# the 13 Delta tables under the same schema; the Volume holds only
# short-lived per-run JSON hand-offs that the retention task GCs.
BV_CATALOG=brickvision                              # the UC catalog BrickVision installs into
BV_SCHEMA=brickvision                               # the single UC schema; partner-overridable
BV_VS_ENDPOINT=brickvision-dev

# --- Mode ---
BV_MODE=local                                        # local | dev | stg | prod
BV_DRY_RUN=true                                      # true = no UC/Delta writes, only log
BV_FAKE_LLM=false                                    # true = mock FMS calls (canned outputs from tests/fixtures/)

# --- Token budget caps (local override of defaults; tokens are primary, USD is advisory) ---
BV_BUDGET_PER_BUILD_INPUT_TOKENS=200000
BV_BUDGET_PER_BUILD_OUTPUT_TOKENS=40000
BV_BUDGET_DAILY_TOKENS=5000000
BV_BUDGET_COST_ALERT_USD=20                          # advisory; emits Question at 80%, never aborts

# --- Per-stage design-pipeline budgets (NEW v0.7.3) ---
BV_BUDGET_DESIGN_SKETCH_INPUT_TOKENS=20000
BV_BUDGET_DESIGN_SKETCH_OUTPUT_TOKENS=4000
BV_BUDGET_DESIGN_PER_SECTION_INPUT_TOKENS=15000
BV_BUDGET_DESIGN_PER_SECTION_OUTPUT_TOKENS=3000
BV_BUDGET_DESIGN_MAX_SECTION_RETRIES=2

# --- Caching (idempotent regeneration) ---
BV_CACHE_LLM_OUTPUTS=enabled                         # disabled forces re-generation of bv:llm-prose blocks
BV_CACHE_DESIGN_SKETCH=enabled
BV_CACHE_DESIGN_SECTIONS=enabled

# --- Logging ---
BV_LOG_LEVEL=DEBUG
BV_TRACE_TO_MLFLOW=true
BV_TRACE_TO_STDOUT=true
```

---

### §15.1.1 Config bundles for workspace profiles and skill packs (NEW v0.7.9)

Partner/customer workspace connection profiles are **not** stored in UC tables and are **not** expanded into `.env`. `.env` carries only bootstrap pointers to YAML config bundles:

```text
config/
  brickvision.yaml
  workspaces/
    partner-dev.workspace.yaml
    customer-a-prod.workspace.yaml
  skill-packs.yaml

skill-packs/
  brickvision-core/
    pack.yaml
    skills/
      uc.catalog-introspect/SKILL.yaml
  partner-acme/
    pack.yaml
    skills/
      telco.churn-feature-builder/SKILL.yaml
```

The active workspace profile answers "which partner workspace may BrickVision inspect or operate against?" and contains:

- workspace host / workspace id / cloud / region
- auth mode
- secret references, never raw secrets
- allowed and blocked catalog scopes
- read-only vs write-capable policy
- Workspace Context Graph introspection toggles

The skill-pack manifest answers "which PS/FDE and partner-authored skills are trusted and enabled for this run?" Current core skills live under the repository's `skills/` tree; partner skills are loaded from separately versioned skill packs. During a run BrickVision records the normalized YAML hashes, active profile id, loaded pack ids, and loaded `SKILL.yaml` hashes in audit rows. UC tables store observations and run facts after authentication succeeds; they do not bootstrap connection identity or skill trust.

Workspace Context Graph refresh is not a UI-triggered scan. Local deploy creates the serverless workspace context refresh Job from `databricks.yml`; that Job reads the active workspace profile scope, emits audit rows into `workspace_claims`, maintains the deduplicated read model `workspace_claims_current`, and publishes `workspace_claims_current_synced` to Lakebase for API/UI reads.

The Console route is named **Workspace Context**, not "Workspace KG". In v0.7.9 this route becomes the starting point for Build Suggestions: the API reads `workspace_claims_current_synced`, joins/ranks candidates against Capability Graph evidence, and returns concrete suggestions with two actions — `Inspect suggestion` and `Plan and build`. `Inspect suggestion` is read-only. `Plan and build` creates an `outcome_execution` from the suggestion's `suggested_outcome_spec` and enters the agent lifecycle in `docs/24-agent-operating-model.md`.

---

## §15.2 Local dev modes

| Mode combo | Behaviour | Use |
|---|---|---|
| `BV_MODE=local + BV_DRY_RUN=true + BV_FAKE_LLM=true` | Run harness against fixture workspace + canned LLM responses; zero cloud cost | Unit tests + CI |
| `BV_MODE=local + BV_DRY_RUN=true + BV_FAKE_LLM=false` | Real FMS calls but no UC/Delta writes | Integration tests of LLM-backed skills |
| `BV_MODE=dev + BV_DRY_RUN=false` | Real workspace, real writes, customer dev catalog | Engineer iteration |
| `BV_MODE=prod + BV_DRY_RUN=false` | Production catalog | CI deploy pipeline only — never local |

### §15.2.1 Test discipline — these flags are the ONLY way to suppress side-effects (NEW v0.7.7)

Per **discipline rule 15** ([`01-overview.md`](./01-overview.md) §0; rationale + examples in [`10-generation-philosophy.md`](./10-generation-philosophy.md) §8.6) BrickVision does not ship mock or fake implementations of internal seams alongside production code. The env-gates above (`BV_MODE`, `BV_DRY_RUN`, `BV_FAKE_LLM`, `BV_LAKEFLOW_DRY_RUN_TIMEOUT_SEC`) are the **only** mechanism by which tests fork the production code path. They are checked inside the production function — there is no second class to maintain.

The three permitted test-control mechanisms (any others are a discipline rule 15 violation and fail the `NoMockOrFakeImplementations()` scorer per [`17-eval-framework.md`](./17-eval-framework.md) §13.3):

| Mechanism | Where it lives | When to reach for it |
|---|---|---|
| **Env-gate flag on the production code path** | The production module reads the flag at function entry (e.g., `if os.getenv("BV_DRY_RUN") == "true": return _log_only(...)`); the test sets the env var via `monkeypatch.setenv` | Suppressing real side-effects (Delta writes, FMS calls, Job submits) on the same code path that runs in production |
| **Fixture data on disk + fixture Delta catalogs** | `tests/fixtures/` (canned LLM responses, sample SDK trees, JSON snapshots) and `<bv>.eval.*_v1` Delta tables ([`17-eval-framework.md`](./17-eval-framework.md) §13.5) | Deterministic, version-controlled input data for replay |
| **`monkeypatch` at the real import surface** | `tests/unit/` and `tests/integration/` test files; never inside `src/` | Swapping a single dependency for one test (e.g., `monkeypatch.setattr("databricks.sdk.WorkspaceClient", ...)`) |

Forbidden under `src/brickvision*/`: `class FakeFoo`/`class MockFoo`/`class StubFoo`/`class DummyFoo`; `Protocol`/`ABC` whose only same-package concrete subclass starts with one of those prefixes; helper modules that pair a Protocol with a TODO ("production wrapper lands in vNext"). See [`10-generation-philosophy.md`](./10-generation-philosophy.md) §8.6.3 for the full forbidden list and §8.6.4 for the migration of any pre-v0.7.7 Protocol+mock seams.

---

## §15.3 Loader precedence

`.env` → environment variables → DAB `variables:` (in deployed mode). Local dev always reads `.env` first; production deployments use DAB vars and ignore `.env` unless `BV_MODE != prod`.

`.env` is loaded via `python-dotenv` at harness startup. The loader rejects `BV_MODE=prod + .env present` (refuses to start) — production must source config from DAB, not from a checked-out file.

For model endpoints, local/dev uses only `LLM_GENERAL_TASKS` and `LLM_EMBEDDING_TASKS`. If production later reintroduces per-role model routing, it must come from an auditable UC routing table and must not add more local `.env` endpoint knobs.

---

## §15.4 Generated harnesses ship `.env.example` too

Every generated harness has its own `.env.example` reflecting its own resource names and `model_routing_overrides`. Keeps the dev-loop story consistent across BrickVision and the harnesses it generates. Generated `.env.example` is templated from `DESIGN.yaml.deploy` ([`04-schemas.md`](./04-schemas.md) §6.3) — same source of truth as `AgentHarness.yaml`.

---

## §15.4.1 Local-mode end-to-end runbook (NEW v0.7.7 — `scripts/local_deploy.sh`)

For partners and developers who want to run the **SPA + FastAPI sidecar locally on their laptop** while keeping the capability indexer + UC tables + Vector Search endpoint in a real Databricks workspace, BrickVision ships a one-shot bootstrap script: [`scripts/local_deploy.sh`](../scripts/local_deploy.sh).

Topology:

```text
┌────────────────────────────┐         ┌──────────────────────────────────┐
│ Your laptop                │   HTTPS │ Databricks workspace              │
│  • Vite SPA   :5173        │ ◀─────▶ │  • bv_indexer_sp / bv_app_sp      │
│  • Uvicorn    :8000        │         │  • <BV_CATALOG>.<BV_SCHEMA>.*     │
│                            │         │      (single flat schema; all     │
│  (start_local_spa.sh)      │         │       Delta tables + the          │
│                            │         │       indexer-state Volume        │
│                            │         │       live here)                  │
│  Reads from:               │         │  • bv_vs_endpoint + entity_index  │
│  1. Lakebase (psycopg)     │         │    (5,841 rows; Direct Access)   │
│     → *_synced tables      │         │  • Lakebase Autoscaling           │
│  2. VS Index (REST)        │         │    (brickvision/main; Synced)    │
│     → semantic search      │         │  • bv_capability_indexer Job      │
│  3. FMS (REST)             │         │    (13-task DAG; nightly 02:00)  │
│     → embed queries        │         │  • Foundation Model APIs          │
│     → code generation      │         │    (qwen3-embedding, claude)     │
└────────────────────────────┘         └──────────────────────────────────┘
```

5-minute runbook:

```bash
cp .env.example .env
$EDITOR .env                                      # set DATABRICKS_HOST + DATABRICKS_TOKEN

bash scripts/local_deploy.sh                      # one-shot, idempotent (~12-15 min)
bash scripts/local_deploy/start_local_spa.sh      # in a separate terminal

open http://localhost:5173/knowledge              # real workspace data
```

What `local_deploy.sh` does (each phase idempotent — safe to re-run):

| Phase | Action |
| ----- | ------ |
| 0     | Preflight: verify `databricks` CLI + Python + `.env` |
| 1.sp  | SCIM-create `bv_indexer_sp` + `bv_app_sp` (skipped if `BV_LOCAL_DEPLOY_AUTO_PROVISION_SPS=false`) |
| 1.whse| Reuse / create the `bv-warehouse` SQL warehouse (skipped if `BV_INDEXER_WAREHOUSE_ID` is set) |
| 1.uc  | `CREATE CATALOG <BV_CATALOG>` + the **single** schema `<BV_CATALOG>.<BV_SCHEMA>` + the indexer-state Volume `<BV_CATALOG>.<BV_SCHEMA>.<BV_INDEXER_STATE_VOLUME>` (default name `indexer-state`; per v0.7.7 schema consolidation — every BrickVision UC object now lives in one flat schema; the Volume is the inter-task JSON hand-off channel for the 13 indexer tasks, NOT a state store) |
| 1.ddl | Apply 13 capability-graph Delta tables via `brickvision_runtime.capability_graph.schemas.ALL_DDL` (rendered with `<BV_CATALOG>.<BV_SCHEMA>.<table>`) |
| 1.budget | Seed `<BV_CATALOG>.<BV_SCHEMA>.budget_namespaces` with `app` + `indexer` rows for §11.4 isolation |
| 1.grant | Grant `SELECT` on schema to `bv_app_sp`; the indexer SP is the schema OWNER (set in 1.uc); `WRITE_VOLUME, READ_VOLUME` on the indexer-state Volume (`<BV_INDEXER_STATE_VOLUME>`) for the indexer SP |
| 1.vs  | Create `bv_vs_endpoint` (~10-15 min cold-create) + 1 Direct Access index (`entity_index`; schema: `id:string`, `entity_id:string`, `entity_kind:string`, `meta_skill_id:string`, `top_order_id:string`, `chunk_text:string`, `source_url:string`, embedding: 1024-dim float vector from `databricks-qwen3-embedding-0-6b`) |
| 2     | `brickvision install` (8 pre-flights, including N180 capability-graph probes) |
| 3     | `databricks bundle deploy --target default` of *only* `bv_capability_indexer` (slim DAB; **no Apps**, **no peripheral Jobs** — `apps:` block stripped from the deploy by [`scripts/local_deploy/deploy_indexer_job.py`](../scripts/local_deploy/deploy_indexer_job.py)) |
| 4     | `brickvision indexer refresh` (first end-to-end run; 15-30 min) |
| 5     | Print operator next steps |

What `local_deploy.sh` deliberately does **not** do:

- Doesn't deploy the BrickVision Visual Builder as a Databricks App. The `apps:` block in `databricks.yml` is stripped from the slim DAB on every deploy. The SPA runs locally via [`scripts/local_deploy/start_local_spa.sh`](../scripts/local_deploy/start_local_spa.sh).
- Doesn't auto-provision an FMS endpoint — your workspace must already have the endpoints named by `LLM_GENERAL_TASKS` and `LLM_EMBEDDING_TASKS`. The `pre_flight.fms_endpoint_present` check tells you if one is missing.
- Doesn't seed example skills. The capability indexer pulls exclusively from the Databricks SDK + REST + docs (per [`docs/23-databricks-capability-graph.md`](./23-databricks-capability-graph.md) §23.2.1) — no manual seeds. The first refresh produces the entire snapshot from upstream sources.

Operator flags:

```bash
bash scripts/local_deploy.sh --doctor          # read-only checklist
bash scripts/local_deploy.sh --skip vs         # skip a single phase
bash scripts/local_deploy.sh --no-trigger      # provision + deploy, no first refresh
bash scripts/local_deploy.sh --resume          # idempotent retry after partial failure
```

Full troubleshooting matrix lives at [`scripts/local_deploy/README.md`](../scripts/local_deploy/README.md).

## §15.5 Install runbook (B8 + B9 closure v0.7.5)

`brickvision install` is the public install command. Two equivalent distribution paths, identical UX:

| Path | Command | When |
|---|---|---|
| **PyPI (default)** | `pip install brickvision[install] --index-url <partner-pypi-proxy> && brickvision install --profile <profile>` | Partner runs from a developer machine or jump host with workspace credentials |
| **Databricks Labs (if approved)** | `databricks labs install brickvision --profile <profile>` | If BrickVision is accepted as a Labs project by Databricks PM; wraps the same CLI under the hood |

The CLI is the single deterministic install path. It does NOT call `databricks bundle deploy` directly — it runs **pre-flights first**, then `bundle deploy`. Each pre-flight is a P7 hard gate; on any miss, the CLI emits a typed Question and aborts (no silent partial install).

```
brickvision install --profile <profile> [--dry-run] [--resume-from <step_id>]
  │
  ├─► pre_flight.mlflow_version             ([`05-build-pipeline.md`](./05-build-pipeline.md) §7.6 MLFLOW_VERSION_TOO_OLD)
  │     └─ asserts mlflow.__version__ >= "3.1" on the install host
  │
  ├─► pre_flight.databricks_sdk_version     (NEW v0.7.6.5 — DATABRICKS_SDK_VERSION_TOO_OLD)
  │     └─ asserts databricks-sdk >= "0.68.0" on the install host (the floor required by
  │        Lakehouse Monitoring + the v0.7.6.4 Workspace Bindings client; per Databricks docs
  │        Lakehouse Monitoring API requires databricks-sdk-py >= 0.68.0). On miss the operator
  │        runs `pip install -U "databricks-sdk>=0.68"` and re-runs install.
  │
  ├─► pre_flight.python_version             (NEW v0.7.6.5 — PYTHON_VERSION_TOO_OLD)
  │     └─ asserts python >= 3.10 on the install host (the floor required for Unity Catalog
  │        Functions used as agent tools per the Databricks UC Functions doc). The runtime
  │        already pins Python 3.11 inside the serverless environments[].spec, but the install
  │        host runs the bundle CLI locally so this floor binds there too.
  │
  ├─► pre_flight.serverless_env_version     (NEW v0.7.6.5 — SERVERLESS_ENV_VERSION_INCOMPATIBLE)
  │     └─ asserts that every Job spec's `environments[].spec.client` resolves to "2" (the
  │        Serverless environment version 2 — Ubuntu 22.04 + Python 3.11.10 + Databricks Connect
  │        15.4.5; release Nov 2024, end-of-support Nov 2027 per the Databricks Serverless
  │        environment versions doc). Future bump to client="3" is a tracked maintenance item.
  │
  # The pre-v0.7.7 pre_flight.routing_table_endpoint_retirement_check + the nightly
  # fms_retirement_calendar_refresher Job + the <bv>.config.fms_retirement_calendar
  # Delta table were retired in the v0.7.7 over-architecture cleanup. The retirement
  # concern is now handled via a hardcoded retirement-date list shipped in
  # model_routing defaults and updated manually each BrickVision release.
  │
  ├─► pre_flight.mlflow_responses_tracing_capability       (NEW v0.7.6.6 — MLFLOW_RESPONSES_TRACING_INCOMPLETE)
  │     └─ Closes the v0.7.6.6 deep-research finding that mlflow.openai.autolog() has known bugs
  │        (mlflow/mlflow#22598 + #22739) when the OpenAI Agents SDK uses the Responses API (its
  │        default since openai-agents 0.13): span inputs contain openai.Omit sentinels and span
  │        outputs are AsyncAPIResponse repr; for streaming runs the fallback
  │        MlflowOpenAgentTracingProcessor writes empty inputs/outputs. Steps:
  │        (1) imports mlflow + openai-agents + agents.tracing in a subprocess;
  │        (2) registers MlflowOpenAgentTracingProcessor via add_trace_processor(...);
  │        (3) runs Runner.run(Agent(name="bv_smoke", instructions="say hi", tools=[])) once
  │            against the workspace's resolved design_section_worker endpoint;
  │        (4) fetches the recorded MLflow trace via the MLflow client;
  │        (5) asserts the root Response span has non-empty inputs.messages AND non-empty
  │            outputs.messages AND both parse as List[Dict[role, content]].
  │        On miss: emit Question with reason MLFLOW_RESPONSES_TRACING_INCOMPLETE,
  │        suggested_next_action="upgrade mlflow to <pinned-floor-with-#22598-fix> OR pin
  │        openai-agents to <known-good-version> OR fall back to path-B unwrapped per
  │        06-design-pipeline.md §7.7.A". Install never silently proceeds with broken tracing.
  │
  ├─► pre_flight.workspace_connectivity     (DATABRICKS_HOST + DATABRICKS_TOKEN reachable)
  │
  ├─► pre_flight.sp_quota                   (H3 closure — [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.1 SP_QUOTA_EXCEEDED)
  │     └─ enumerates declared SPs vs current workspace SP count + safety margin (default 50)
  │
  ├─► pre_flight.vector_search              (B6 closure — VS_OUT_OF_BAND_PROVISIONING_REQUIRED + VS_RESOURCE_SCHEMA_MISMATCH)
  │     ├─ existence check (endpoints + indexes — including the v0.7.6 retrieval indices below)
  │     └─ schema check    (embedding_model_endpoint, dimension, primary_key, schema)
  │
  ├─► pre_flight.embedding_role_resolution  (NEW v0.7.6 — EMBEDDING_ROLE_UNRESOLVED)
  │     └─ asserts the embedding_default symbolic role resolves to a live FMS embedding endpoint
  │        whose dimension matches the schemas declared for both VS indices in 04-schemas.md §6.4
  │
  ├─► pre_flight.eval_fixtures              (NEW v0.7.6.1 — EVAL_FIXTURES_NOT_LOADABLE)
  │     └─ loads the 4 curated gold sets from src/brickvision/install/eval_fixtures/gold_*.jsonl
  │        and validates schemas against the declarations in 17-eval-framework.md §13.5
  │        (gold_kg_search_v1, gold_kg_walk_v1, gold_mentions_v1, gold_docs_lookup_urls_v1).
  │        Fixtures missing or schema-divergent → emit Question, abort install (P7).
  │
  ├─► pre_flight.visual_builder_assets       (NEW v0.7.6.1 — VISUAL_BUILDER_ASSETS_MISSING)
  │     └─ asserts the React Flow + canvas wrapper JS bundle is on disk at
  │        src/brickvision/apps/console/visual_builder/components/dist/canvas.js
  │        and the SHA matches the version pin in pyproject.toml extras
  │        (per 12-visual-builder.md §10.2.1 item 2 — no CDN load in Databricks Apps).
  │        Skipped if BV_INSTALL_VISUAL_BUILDER=false (forms-only install).
  │
  ├─► pre_flight.mosaic_gateway             (MOSAIC_GATEWAY_NOT_PROVISIONED for prod-mode installs)
  │     └─ asserts every declared LLM endpoint is fronted by Mosaic AI Gateway when BV_MODE in {stg, prod}
  │
  ├─► pre_flight.dab_offline_validate       (databricks bundle validate --offline)
  │
  ├─► pre_flight.serverless_only_compute    (NEW v0.7.6.2 — NON_SERVERLESS_COMPUTE_DECLARED)
  │     └─ parses the rendered DAB; rejects any task spec containing existing_cluster_id, new_cluster,
  │        or job_cluster_key; rejects any model_serving block missing the serverless flag; rejects
  │        any apps block with non-serverless compute. Discipline rule 12 (01-overview.md §0):
  │        all BrickVision compute is Databricks Serverless. P7: emit Question + abort, never
  │        silently accept a job-cluster declaration.
  │
  ├─► pre_flight.replay_retention_consistent  (NEW v0.7.6.3 — REPLAY_RETENTION_INSUFFICIENT)
  │     └─ for every replayable Delta table (<bv>.kg.claims, <bv>.kg.subject_cards, <bv>.audit.events,
  │        <bv>.config.model_routing, <bv>.builds.runs, <bv>.cache.*), reads TBLPROPERTIES and asserts
  │        delta.deletedFileRetentionDuration AND delta.logRetentionDuration BOTH >= BV_REPLAY_MAX_AGE_DAYS
  │        (default 90 days). Drift emits REPLAY_RETENTION_INSUFFICIENT with the per-table actual values
  │        and the single ALTER TABLE statement to fix. P7: emit Question + abort, never silently let
  │        partner installs accumulate replay-incapable history.
  │
  ├─► pre_flight.write_side_skill_constitutional_coverage  (NEW v0.7.6.3, REVISED v0.7.6.4 — WRITE_SIDE_CONSTITUTIONAL_COVERAGE_MISSING)
  │     └─ for each of the 5 write-side Layer-0 skills (delta.pyspark-transform, delta.sql-transform,
  │        ml.train-evaluate-register, ml.assign-alias NEW v0.7.6.4, ml.serve-deploy), reads SKILL.yaml
  │        and asserts the constitutional rules block references the appropriate rule:
  │        no.write.to.production.catalog.without.approval (4 skills; ml.serve-deploy uses the deploy variant);
  │        no.model.serving.deploy.without.approval (ml.serve-deploy);
  │        write.target.catalog.must.be.bound.read.write.to.executing.workspace (ALL 5 — RENAMED v0.7.6.4
  │        from no.cross.workspace.write).
  │        Missing or mistyped rule references are an install-time block (NOT a runtime block — the rule
  │        MUST be declared at install time so the runtime check is provably wired up).
  │
  ├─► pre_flight.write_target_catalog_bound_rw  (NEW v0.7.6.4 — WRITE_TARGET_CATALOG_NOT_BOUND_RW)
  │     └─ delegates to UC's metastore-native Workspace-Catalog Bindings primitive. For each catalog
  │        in the partner's declared write-target list (<bv>.config.write_target_catalogs — populated at
  │        install time from the partner profile, default empty in dev/local mode):
  │        1. Resolve executing workspace ID via Databricks SDK: brickvision_runtime/core/workspace.py::
  │           get_executing_workspace_id() → WorkspaceClient().get_workspace_id().
  │        2. Call GET /api/2.1/unity-catalog/workspace-bindings/{catalog}.
  │        3. Assert the executing workspace ID appears with binding_mode=BINDING_MODE_READ_WRITE.
  │        4. Cache the result for BV_UC_BINDINGS_PRE_FLIGHT_CACHE_TTL_SEC (default 300).
  │        On miss: emit WRITE_TARGET_CATALOG_NOT_BOUND_RW with the exact `databricks workspace-bindings update`
  │        CLI command the partner administrator should run. P7: install proceeds in dev/local but write-side
  │        skill runtime calls will fail with the same code; install in stg/prod blocks at this pre-flight.
  │
  ├─► pre_flight.write_side_fixture_workspace  (NEW v0.7.6.3 — WRITE_SIDE_FIXTURE_NOT_LOADABLE)
  │     └─ loads <bv>.eval.write_side_fixture_v1 from src/brickvision/install/eval_fixtures/
  │        write_side_fixture_v1/ (EXTENDED v0.7.6.4: 12 input tables + 6 expected output tables + 2 ML
  │        training fixtures + 1 fixture UC Registered Model with 3 versions for alias scenarios + 1
  │        fixture Databricks Model Serving endpoint per 17-eval-framework.md §13.5). Schema-validates.
  │        If write-side skills are excluded from this install (BV_INSTALL_WRITE_SIDE_SKILLS=false),
  │        this pre-flight is skipped.
  │
  ├─► pre_flight.lakeflow_pipeline_create_quota  (NEW v0.7.6.4 — LAKEFLOW_QUOTA_INSUFFICIENT)
  │     └─ if BV_INSTALL_WRITE_SIDE_SKILLS=true and any write-side skill in the install includes a
  │        delta.{pyspark,sql}-transform shape, calls GET /api/2.0/pipelines and asserts the partner's
  │        Lakeflow pipeline quota is sufficient for the declared concurrent build limit (default 4
  │        concurrent Lakeflow pipelines for builds). On miss: emit LAKEFLOW_QUOTA_INSUFFICIENT with the
  │        Databricks support contact; partner can request a quota increase or lower the concurrent
  │        build limit via BV_MAX_CONCURRENT_LAKEFLOW_PIPELINES.
  │
  ├─► pre_flight.indexer_sp_provisioned       (NEW v0.7.7 — INDEXER_SP_NOT_PROVISIONED)
  │     └─ asserts the bv_indexer_sp service principal exists in the workspace AND is distinct from
  │        bv_app_sp (i.e., the partner has not collapsed the two SPs to a single one for cost reasons).
  │        Calls GET /api/2.0/preview/scim/v2/ServicePrincipals?filter=displayName+eq+"bv_indexer_sp" and
  │        verifies the resulting `application_id` does not equal the bv_app_sp's. On miss: emit
  │        INDEXER_SP_NOT_PROVISIONED with the `databricks service-principals create --display-name
  │        bv_indexer_sp` CLI command + the grant block per 23-databricks-capability-graph.md §23.3.5.
  │        P7: install in stg/prod blocks; install in dev/local proceeds (the indexer Job is
  │        deployed but disabled per BV_INDEXER_SCHEDULE_DISABLED=true in dev/local).
  │
  ├─► pre_flight.indexer_budget_namespace_isolated  (NEW v0.7.7 — INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED)
  │     └─ asserts the BudgetGuard configuration in <bv>.config.budget_namespaces has both "app" and
  │        "indexer" namespaces with non-overlapping ledger tables; verifies the
  │        BV_BUDGET_NAMESPACE env var resolution at app/indexer SP boundaries (the install-time check
  │        runs both as bv_app_sp and as bv_indexer_sp via assume-SP and confirms each resolves to its
  │        own namespace's daily token cap). On miss: emit INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED with the
  │        DDL fragment to remediate. The check is the install-time form of BudgetNamespaceIsolation()
  │        scorer (17-eval-framework.md §13.3).
  │
  ├─► pre_flight.uc_schema_capability_graph_ownership  (NEW v0.7.7 — UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID)
  │     └─ asserts the <bv>.capability_graph schema exists AND the OWNER is bv_indexer_sp AND bv_app_sp
  │        has only SELECT (NOT MODIFY/CREATE) on the schema. Per 23-databricks-capability-graph.md §23.3.5
  │        the indexer is the sole writer; the app reads. Calls GET /api/2.1/unity-catalog/permissions/
  │        schema/<bv>.capability_graph and verifies the grant set. On miss: emit
  │        UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID with the GRANT/REVOKE statements to remediate.
  │
  ├─► pre_flight.vector_search_endpoint_grants  (NEW v0.7.7 — VS_ENDPOINT_GRANTS_MIXED)
  │     └─ asserts the shared bv_vs_endpoint has bv_indexer_sp WRITE grant on the 3 capability-graph
  │        indexes (meta_skills_index, extensions_index, capability_passages_index) AND bv_app_sp
  │        READ-ONLY grant on the same. Calls GET /api/2.0/vector-search/endpoints/bv_vs_endpoint and
  │        cross-references with each index's permissions. The default per 23-databricks-capability-graph.md
  │        §23.5.2 (D15) is endpoint-sharing across the existing 2 v0.7.6 indexes + the 3 capability-graph
  │        indexes; partners with cost concerns can opt to a separate bv_indexer_vs_endpoint and the check
  │        adapts. On miss: emit VS_ENDPOINT_GRANTS_MIXED with the grant set to remediate.
  │
  ├─► databricks bundle deploy
  │     ├─ each step writes a Claim into <bv>.install.events with the step_id
  │     ├─ on failure mid-deploy: emit INSTALL_PARTIAL_STATE Question with successful_steps[]
  │     │                        and the deterministic next action (resume vs uninstall+reinstall)
  │     └─ on success: tables created (including <bv>.kg.{claims,beliefs,questions} and the rest
  │                    of the UC layout in 18-architecture.md §14.3)
  │
  ├─► post_deploy.create_empty_vs_indices   (NEW v0.7.6 + revised v0.7.6.1)
  │     ├─ creates the empty Delta projection <bv>.kg.subject_cards (DDL per 04-schemas.md §6.4.2;
  │     │   appendOnly=false; CDF enabled so the VS DA index can read it)
  │     ├─ creates <bv>.kg.claims_text_index    (Direct Access; embedding_default; over subject_cards)
  │     └─ creates <bv>.kg.doc_passages_index   (Direct Access; embedding_default; over kg.claims where predicate=CONTENT)
  │     │   Both indices start empty. They are populated incrementally by the two CDC consumer Jobs
  │     │   started in the next steps. No bulk doc indexing happens at install time.
  │     │   There is no "reference corpus" step.
  │     └─ on failure: emit VS_INDEX_CREATION_FAILED with the underlying VS API error
  │
  ├─► post_deploy.start_subject_card_materializer  (NEW v0.7.6.1; serverless-pinned v0.7.6.2 — SUBJECT_CARD_MATERIALIZER_FAILED_TO_START)
  │     └─ enables the Databricks Serverless Job (sutra-style spark_python_task +
  │        environments[].spec.{client="2", dependencies=[...]} — same shape as
  │        scripts/launch.py in the substrate repo) running brickvision_runtime/kg/
  │        subject_card_materializer.py with Trigger.AvailableNow on the DAB-declared
  │        60s cron. Reads CDF on <bv>.kg.claims (predicate != CONTENT); writes
  │        <bv>.kg.subject_cards; embeds card_text and pushes to
  │        <bv>.kg.claims_text_index. Each Job run terminates with exactly one
  │        checkpoint Question commit. Discipline rule 12: no job_clusters block.
  │
  ├─► post_deploy.start_index_maintainer    (NEW v0.7.6; serverless-pinned v0.7.6.2 — INDEX_MAINTAINER_FAILED_TO_START)
  │     └─ same shape as above for the doc-passages-side CDC consumer
  │        (brickvision_runtime/kg/index_maintainer.py): Databricks Serverless Job,
  │        spark_python_task, Trigger.AvailableNow, 60s DAB cron, environments[].spec
  │        per discipline rule 12. Reads CDF on <bv>.kg.claims (predicate = CONTENT);
  │        embeds chunk text; pushes to <bv>.kg.doc_passages_index.
  │
  ├─► post_deploy.write_kg_search_defaults  (NEW v0.7.6.1 — KG_SEARCH_DEFAULTS_NOT_WRITABLE)
  │     └─ writes <bv>.config.kg_search_defaults from the values resolved in Phase -1 N0-12.10
  │        (W_STRUCT, W_DOC, max_subgraph_edges, ppr_alpha, max_iter, tol, kg_search_enabled)
  │        signed by admin SP. If N0-12.10 selected the OFF cut-line, kg_search_enabled=false
  │        is written here and BV_KG_SEARCH_ENABLED in .env is overridden in stg/prod modes.
  │
  ├─► post_deploy.start_orphan_watcher       (NEW v0.7.6.3 — ORPHAN_WATCHER_FAILED_TO_START)
  │     └─ enables the Databricks Serverless Job running brickvision_runtime/build_resume/
  │        orphan_watcher.py with Trigger.AvailableNow on the DAB-declared 5-minute cron.
  │        Polls <bv>.builds.runs for status='running' AND
  │        now() - last_audit_event_at > BV_BUILD_LIVENESS_TIMEOUT_SEC; for each, transitions
  │        to 'paused_orphaned' and emits BUILD_COORDINATOR_DIED_DURING_RUN Question
  │        with the last completed BuildStepCompleted boundary so resume target is unambiguous.
  │        Required for build-resume contract (16-identity-audit-replay.md §12.4 invariant 8).
  │
  ├─► post_deploy.start_hitl_watcher         (NEW v0.7.6.3 — HITL_WATCHER_FAILED_TO_START)
  │     └─ enables the Databricks Serverless Job running brickvision_runtime/hitl/watcher.py
  │        with Trigger.AvailableNow on the DAB-declared 1-minute cron (BV_HITL_WATCHER_INTERVAL_SEC).
  │        Polls <bv>.builds.runs for status='paused_hitl'; for each, reads the HITL approval row;
  │        on approval, writes HITLApproved Claim that triggers a coordinator wake-up; on TTL miss,
  │        transitions to 'failed' with HITL_APPROVAL_TIMEOUT (16-identity-audit-replay.md §12.4
  │        invariant 10).
  │
  ├─► post_deploy.write_constitutional_rules (NEW v0.7.6.3, REVISED v0.7.6.4 — CONSTITUTIONAL_RULES_NOT_WRITABLE)
  │     └─ writes the 3 rule rows (with the v0.7.6.4-renamed cross-workspace rule
  │        `write.target.catalog.must.be.bound.read.write.to.executing.workspace`) + the
  │        production_catalog_patterns table starter rows per 04-schemas.md §6.5.4 to <bv>.policy.rules
  │        and <bv>.policy.production_catalog_patterns.
  │
  ├─► post_deploy.write_production_aliases (NEW v0.7.6.4 — PRODUCTION_ALIASES_NOT_WRITABLE)
  │     └─ writes the partner-declared production-tier UC alias names to <bv>.policy.production_aliases
  │        per 04-schemas.md §6.5.4.1. Default seed in stg/prod modes = ["champion"]; partners may
  │        extend at install time via the install profile. Default in dev/local mode = empty (all
  │        alias names are experimental — no HITL required for ml.assign-alias in dev).
  │
  ├─► post_deploy.write_ml_replay_tolerance_defaults (NEW v0.7.6.4 — ML_REPLAY_TOLERANCE_DEFAULTS_NOT_WRITABLE)
  │     └─ writes the per-(model_family, metric_name) tolerance rows to
  │        <bv>.config.ml_replay_tolerance_defaults per 04-schemas.md §6.5.7. Library-deterministic
  │        families (sklearn, xgboost, lightgbm) seed at 0% (byte-identical given pinned env).
  │        spark.mllib.* post-canonicalization seed at 1% relative + 0.01 AUC absolute per the FINRA
  │        finding. Partners override via the install profile or via direct INSERT post-install.
  │        Signed by admin SP. Skipped if BV_INSTALL_WRITE_SIDE_SKILLS=false.
  │
  ├─► post_deploy.write_cache_eviction_policy (NEW v0.7.6.3 — CACHE_EVICTION_POLICY_NOT_WRITABLE)
  │     └─ writes default eviction policy per cache table to <bv>.config.cache_eviction_policy
  │        (e.g., max_age_days=BV_REPLAY_MAX_AGE_DAYS for cache.llm_outputs); signed by admin SP.
  │        Auto-eviction is forbidden; partners run `brickvision cache prune --before <date>` per §15.6.
  │
  ├─► post_deploy.start_serving_alias_drift_watcher (NEW v0.7.6.5 — SERVING_ALIAS_DRIFT_WATCHER_FAILED_TO_START)
  │     └─ starts the v0.7.6.5 serverless Job `brickvision_runtime/ml/serving_alias_drift_watcher.py` per
  │        18-architecture.md §14.1. Spec: spark_python_task + environments[].spec.{client="2", dependencies}
  │        + Trigger.AvailableNow + 5-min DAB cron per discipline rule 12. Asserts the Job is registered + the
  │        first run completes successfully + writes a serving_alias_drift_watcher_started Claim signed by
  │        agent:serving_alias_drift_watcher SP. Closes the buildability gap surfaced by the v0.7.6.5 deep
  │        research that Databricks Model Serving's `served_entities[]` does not auto-track alias re-points
  │        at the endpoint (per the create-endpoint API ref the field is entity_version, no entity_alias).
  │        Skipped if BV_INSTALL_WRITE_SIDE_SKILLS=false (no endpoints to watch).
  │
  ├─► post_deploy.start_capability_indexer  (NEW v0.7.7 — CAPABILITY_INDEXER_FAILED_TO_START)
  │     └─ registers the bv_capability_indexer multi-task serverless Job per 23-databricks-capability-graph.md
  │        §23.3 (15 tasks; serverless compute exclusively; runs as bv_indexer_sp; budget namespace "indexer";
  │        writes to <bv>.capability_graph.* schema; uses shared bv_vs_endpoint with WRITE on capability indexes
  │        only). DAB declares the cron from BV_INDEXER_SCHEDULE_CRON (default `0 2 * * *`). On install, runs the
  │        first refresh ON-DEMAND immediately (not waiting for the cron) so the partner gets an active snapshot
  │        before the first build pipeline call; writes the v1 baseline to <bv>.capability_graph.smoke_baseline
  │        from the N0-12.16-curated 5-query golden set. Skipped if BV_INDEXER_SCHEDULE_DISABLED=true (the Job is
  │        deployed but unscheduled; partner triggers refreshes via `brickvision indexer refresh` CLI).
  │        Asserts the first run reaches the promote_snapshot task successfully (writes the active snapshot row);
  │        on miss → emit CAPABILITY_INDEXER_FAILED_TO_START with the failed task's reason code.
  │
  ├─► post_deploy.start_capability_graph_retention  (NEW v0.7.7 — CAPABILITY_GRAPH_RETENTION_FAILED_TO_START)
  │     └─ starts the v0.7.7 serverless Job `brickvision_runtime/databricks_jobs/run_capability_graph_retention.py`
  │        per 23-databricks-capability-graph.md §23.4.2 + 18-architecture.md §14.1. Spec: spark_python_task +
  │        environments[].spec + Trigger.AvailableNow + daily DAB cron (separate Job from the indexer DAG so
  │        retention runs even if the indexer is paused). Prunes Tier A snapshots older than 30 days + their
  │        corresponding Tier B rows; emits CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION on rollback request to
  │        a deleted snapshot. Skipped if BV_INDEXER_SCHEDULE_DISABLED=true.
  │
  └─► post_deploy.verify_install            (NEW v0.7.6 — INSTALL_VERIFICATION_FAILED; revised v0.7.6.3)
        ├─ asserts kg_search returns zero seeds with KG_SEARCH_NO_SEEDS (NOT a silent empty list)
        │   when called against the empty KG; this is the positive expected behaviour for a fresh install
        │   (skipped if kg_search_enabled=false; instead asserts kg_retrieve + kg_walk both return empty)
        ├─ asserts skill:docs.lookup cache-miss path works against a known-stable test URL from
        │   <bv>.eval.gold_docs_lookup_urls_v1
        ├─ asserts both maintainers have committed at least one checkpoint Question
        │   (proves they're running, not just started)
        ├─ asserts orphan watcher and HITL watcher have each emitted at least one liveness Claim
        │   (NEW v0.7.6.3 — proves the watchers are running)
        ├─ asserts the 3 constitutional rules are present in <bv>.policy.rules with valid signatures
        │   (NEW v0.7.6.3 — skipped if BV_INSTALL_WRITE_SIDE_SKILLS=false)
        ├─ asserts the cache_eviction_policy rows are present and signed
        │   (NEW v0.7.6.3)
        └─ on success: emit signed Claim (install:<id>, INSTALLED, true)
```

**Resume semantics.** `brickvision install --resume-from <step_id>` reads `<bv>.install.events`, replays from the named step. Re-running already-successful steps is a P7 violation (silent redo masks errors); the resume command refuses unless `--force-redo` is also passed (which itself emits a `policy_violation` audit row).

**Dry-run.** `brickvision install --dry-run` runs all pre-flights, prints what would be created (DAB plan + VS CLI commands + SP creation list), but takes no mutating action. Useful for SI partner pre-engagement reviews.

**Uninstall.** `brickvision uninstall [--partial]` is the inverse. `--partial` is honest about what gets left behind: VS endpoints (out-of-band, not in DAB) and Lakehouse Monitoring schedules (out-of-band) require explicit operator confirmation per resource — never auto-deleted (P7).

### 15.5.1 What `brickvision install` deliberately does NOT do (NEW v0.7.6)

To prevent confusion: the install creates **empty** infrastructure for the knowledge layer. It does not bulk-fetch or pre-populate any documentation.

- **No bulk doc fetch.** There is no "reference corpus" build step. Databricks docs / MLflow docs / Anthropic eng blog / Mosaic AI Agent Framework docs / Lakebridge docs are pulled on demand by `skill:docs.lookup` ([`11-skill-catalog.md`](./11-skill-catalog.md) §9.1.1) when meta-skills actually need them. This is a deliberate v0.6 design decision — bulk indexing creates a refresh-staleness problem we don't want to own; on-demand fetch makes provenance and replay clean.
- **No KG seed beyond what Layer-0 skills will emit.** The cold-start seed Beliefs from `databricks.sdk` introspection mentioned in [`01-overview.md`](./01-overview.md) §0 are emitted by `skill:uc.catalog-introspect` etc. running as part of the first nightly refresh (or invoked manually post-install), not by the installer.
- **No edges materialised by the installer.** The graph is the Claim log; edges are simply Claims whose `value` is a `subject_id`. No separate edge table; no projection job to run at install time.
- **No predicate vocabulary table populated.** The closed predicate vocabulary lives as a Python enum ([`04-schemas.md`](./04-schemas.md) §6.4); changing it is a versioned schema migration, not an install-time INSERT.

What the install *does* leave in place (revised v0.7.6.3): empty UC catalog + schemas, signed SP roster, signing keys in Secrets, the empty `<bv>.kg.subject_cards` Delta projection, two empty Direct-Access VS indices, the two CDC consumer Jobs (`subject_card_materializer` + `index_maintainer`), the orphan watcher + HITL watcher Jobs (NEW v0.7.6.3 — both serverless per discipline rule 12), the 4 + 2 curated gold Delta tables for the knowledge-layer + model-pick scorers (and `<bv>.eval.write_side_fixture_v1` if write-side skills are installed), the signed `<bv>.config.kg_search_defaults` row, the 3 v0.7.6.3 constitutional rules in `<bv>.policy.rules` + the production-catalog patterns table, and the cache eviction policy table. From this point forward the KG grows organically as skills are invoked.

---

## §15.6 The `brickvision` CLI (NEW v0.7.6.3 — operations runbook)

The CLI is the operator's interface to BrickVision state mutations that are NOT auto-driven by the build pipeline. Every subcommand emits an audit row + a Claim before the mutation executes (state invariant 2 from [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.4).

### 15.6.1 `brickvision build --resume <build_run_id>` (build-resume mechanism)

Resume a build that died mid-execution (coordinator process killed, container re-allocated, partner workspace restart). The CLI:

1. Reads `<bv>.builds.runs.<build_run_id>`; asserts `status in {'running', 'paused_orphaned', 'paused_hitl'}`. Refuses if `status in {'success', 'partial', 'failed'}` (not resumable; user must re-run from scratch via `brickvision build <goal>`).
2. Reads the most recent `BuildStepCompleted` Claim under subject `build:<build_run_id>:step:*` from `<bv>.kg.claims`. If none found → emits `BUILD_RESUME_BOUNDARY_NOT_FOUND` Question and exits non-zero (P7 — without an unambiguous resume target the operator must abandon and re-run).
3. Asserts the routing-table `routing_table_version_hash` from the last audit row of this build still resolves to live endpoints (per [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.3.1 pin 1). Drift → emits `RESOLVED_ENDPOINT_UNAVAILABLE` and offers the 3 v0.7.5 deterministic options.
4. Sets `<bv>.builds.runs.<id>.resumed_from_step = '<step_id>'`, `status = 'running'`, `last_audit_event_at = now()`. Spawns a fresh coordinator process (serverless Job) with `--resume_from_step <step_id>` and the original build_run_id; the coordinator picks up at step+1 with byte-identical token-budget state read from `<bv>.audit.events`.
5. Validated by `BuildResumeIdempotence()` scorer ([`17-eval-framework.md`](./17-eval-framework.md) §13.3): every Layer-0 + meta-skill is tested for resume idempotence in CI.

```bash
$ brickvision build --resume 7c1d4e8f-9a2b-4c5d-8e6f-3a1b9c2d4e5f
Resuming build: status was 'paused_orphaned' (orphaned at 2026-04-21T14:35:08Z, BUILD_LIVENESS_TIMEOUT_EXCEEDED)
Last completed boundary: build:7c1d4e8f.../step:9 (stage:agent-evaluate.stage_b — 2026-04-21T14:34:51Z)
Routing table version hash: matches live (no drift)
Spawning coordinator: agent:builder@v0.6.0 → step:10 (stage:agent-evaluate.stage_c)
Tokens spent so far: 142,300 input / 28,500 output (read from audit; matches budget pre-death)
[follow build at: https://<workspace>/sql/dashboards/builds/7c1d4e8f...]
```

### 15.6.2 `brickvision build --abandon <build_run_id>`

Mark a build as `failed` with reason code `BUILD_ABANDONED_BY_OPERATOR`. Used when the operator decides a `paused_orphaned` build is not worth resuming (e.g., the goal text changed; partner pivoted). Emits the audit row + transitions status. Does NOT delete any underlying state — the build run remains queryable, the audit history immutable.

### 15.6.3 `brickvision cache prune --before <date> [--cache-table <fqn>] [--dry-run]`

The **only** legal eviction path for `<bv>.cache.*` rows. Reads `<bv>.config.cache_eviction_policy` to determine which tables are eligible; respects per-table `max_age_days` floor (refuses to evict newer rows). For every evicted batch, writes a `cache_eviction` audit row signed by the invoking principal.

```bash
$ brickvision cache prune --before 2026-01-21 --dry-run
Cache prune plan (dry-run):
  <bv>.cache.llm_outputs:       2,341 rows older than 2026-01-21 (max_age_days=90 in policy)
  <bv>.cache.design_sketches:     147 rows older than 2026-01-21
  <bv>.cache.design_sections:     893 rows older than 2026-01-21
  <bv>.cache.prose:                52 rows older than 2026-01-21
  Total: 3,433 rows
  Audit rows that will be emitted: 4 (one per cache table)
Run without --dry-run to execute.
```

P7-aligned: there is no auto-prune cron, no Spark Delta vacuum that touches cache tables, no LRU eviction. The operator decides; the audit log records.

### 15.6.4 `brickvision hitl approve <approval_id>` and `brickvision hitl reject <approval_id> --reason <text>`

HITL surface for operators to act on `paused_hitl` builds from the CLI (the visual builder console exposes the same actions in the UI per [`12-visual-builder.md`](./12-visual-builder.md)). Writes a `hitl_decision` audit row signed by the invoking principal; the HITL watcher Job picks it up within `BV_HITL_WATCHER_INTERVAL_SEC` and resumes the build.

### 15.6.5 `brickvision build --replay <build_run_id> [--step <step_id>]`

Reads the audit history for the build and replays it under the 5 replay-pin contracts ([`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.3.1). Without `--step`, replays the entire build; with `--step`, replays only the named step. Outputs the replay viewer URL; surfaces every divergence as a typed Question.

### 15.6.6 `brickvision indexer <subcommand>` (NEW v0.7.7 — capability graph operations)

The operator's interface to the v0.7.7 Capability Indexer Job. Every subcommand emits an audit row + a Claim under subject `indexer:<run_id>` before the mutation per state invariant 2 of [`16-identity-audit-replay.md`](./16-identity-audit-replay.md) §12.4.

**`brickvision indexer refresh [--full]`** — manually triggers `bv_capability_indexer` (on-demand outside the cron schedule). Emits `CAPABILITY_GRAPH_MANUAL_REFRESH` Claim. With `--full` flag, the indexer runs every source's full re-crawl path (ignores the etag-skip cache); without flag, it runs the standard incremental refresh. Refuses if the most recent refresh is in-progress (no concurrent runs). Useful when the partner has just installed a new `databricks-sdk` version or after the v0.7.8 backlog adds a new corpus source.

**`brickvision indexer rollback --to <snapshot_id>`** — atomically promotes a previous snapshot. Rate-limited to 1 rollback per hour per `BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC` (default 3600); over-rate emits `CAPABILITY_GRAPH_ROLLBACK_RATE_LIMITED`. Refuses if `<snapshot_id>` is older than 30 days (Tier A retention deleted; emits `CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION`). Emits `CAPABILITY_GRAPH_MANUAL_ROLLBACK` Claim with the previous active snapshot ID + the new active one. The rollback is observable in `<bv>.capability_graph.snapshot_history` with `deactivated_by_operator=true`.

**`brickvision indexer status`** — prints the current active snapshot ID, snapshot age, partial-source list (if any), the rolling 30-day smoke pass-rate, refresh duration p95, and SDK coverage percentage. Read-only; does not emit any Claim or audit row.

**`brickvision indexer health`** — runs the bet criterion 13 falsification check against `<bv>.capability_graph.corpus_health` rolling 30-day window: prints PASS/FAIL/AT-RISK for each of (13a) SDK coverage ≥ 90%, (13b) smoke top-1 hit-rate ≥ 0.95 baseline, (13c) refresh p95 ≤ 15 min. AT-RISK is emitted when the rolling 7-day window crosses the threshold but the rolling 30-day has not yet (early-warning). Useful as a release-cadence GA-gate sanity check.

```bash
$ brickvision indexer status
Active snapshot: 2026-05-04T02:00:00Z (snapshot_id=01c7f9d4-...)
Snapshot age: 13.2 hours (under tolerance — BV_INDEXER_FRESHNESS_TOLERANCE_DAYS=1)
Partial sources: none (last 5 refreshes)
Rolling 30-day smoke pass-rate: 0.96 (vs 0.95 baseline → PASS)
Rolling 30-day refresh duration p95: 8.7 min (vs 15.0 min SLO → PASS)
SDK coverage: 92.4% (vs 90% threshold → PASS)
Bet criterion 13 status: GREEN
```

P7-aligned: there is no auto-rollback path. Smoke regressions in the indexer DAG cause the new snapshot to be discarded (the previous active stays); a manual rollback is the only path to a previous-but-NOT-most-recent snapshot.

