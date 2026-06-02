# BrickVision

BrickVision is a Databricks meta-skills framework. It builds shared context
across Databricks capabilities, enterprise workspace data, and reusable skills,
then uses HippoRAG2-style retrieval and evaluation loops to propose, plan, and
validate business usecases.

The current implementation is a Capability Graph indexer plus a React/FastAPI
console, workspace evidence views, usecase planning screens, skill contracts,
and MLflow-backed evaluation.

It is not a finished product. Some pieces are operational, some are early MVI,
and some are still design direction captured in docs.

## Current Status

What is real in this repo today:

- A Databricks Asset Bundle (`databricks.yml`) that declares:
  - `bv_capability_indexer`
  - `bv_workspace_context_refresh`
  - `bv_evaluation_scorers`
  - a minimal `brickvision_console` App stanza
- A 14-task Capability Graph indexer under
  `src/brickvision_runtime/capability_graph/` and
  `src/brickvision_runtime/databricks_jobs/run_capability_indexer.py`.
- A FastAPI sidecar under `apps/console-api/`.
- A Vite/React console under `apps/console/`.
- A set of hand-authored skill contracts under `skills/`.
- Evaluation scripts and JSONL evalsets under `scripts/` and `config/evaluation/`.

What is still partial:

- The agent execution lifecycle is not a complete autonomous production runtime.
- Evaluation is useful but uneven: retrieval workflows have runtime checks;
  workspace/usecase/skill/platform categories are still mostly dataset-readiness
  gates.
- MLflow GenAI Agent Evaluation is available as an opt-in path, not the default
  production gate.
- Live trace coverage depends on `BV_MLFLOW_EVALUATION_EXPERIMENT_ID` being set
  in the API runtime.
- The supported console path today is the local Vite/FastAPI launcher. The
  Databricks App definition exists in the bundle but is not the primary deployed
  path yet.
- Several docs still contain historical architecture language from older
  build-pipeline / visual-builder iterations.

## What BrickVision Does

### Capability Graph

BrickVision builds a three-level taxonomy:

```text
Top-Order Skill -> Meta-Skill -> Extension
```

The indexer pulls from five Databricks-related sources:

- Databricks Python SDK
- Databricks REST/OpenAPI/API-reference evidence
- Databricks docs
- Databricks blog content
- Databricks Labs / Lakebridge

The graph builder is mostly deterministic. Blog/meta-skill linkage can use an
LLM extractor when enabled. The indexer stages task outputs in a UC Volume,
persists graph rows to Delta tables, updates Mosaic AI Vector Search, and
publishes Lakebase synced tables for low-latency console reads.

The current graph UI lives at `/knowledge`. It includes search, grounded ask,
top-order/meta-skill/extension browsing, provenance, health, and a Capability
Explorer heatmap.

### Knowledge / RAG

The Knowledge page lets users:

- search the active Capability Graph through Vector Search;
- ask grounded Databricks capability questions;
- inspect retrieved evidence and provenance;
- emit `rag_search` and `rag_answer` evaluation events.

`rag_answer` can log MLflow traces when the API process has
`BV_MLFLOW_EVALUATION_EXPERIMENT_ID` configured and `mlflow-skinny>=3.0`
installed.

### Workspace Context

The workspace context job reads the configured workspace profile, emits workspace
claims, materializes current claims, and publishes synced tables for the console.

The Workspace page is evidence-first. It shows what BrickVision knows about the
workspace and can generate build suggestions from that evidence. This is not a
general workspace scanner UI; it depends on the refresh job and Lakebase sync.

### Usecases

Usecase pages turn workspace evidence into candidate business opportunities.
They separate:

- candidate outcome;
- workspace evidence;
- required skill families;
- missing inputs;
- generated or planned technical artifacts;
- execution history.

This area is active MVI work. It is not yet a full multi-stage autonomous
delivery engine.

### Skills

The repo contains hand-authored `SKILL.yaml` contracts under `skills/`.
They define identity, inputs, outputs, eval expectations, safety constraints,
dependencies, and Capability Graph linkage via `exemplar_of`.

These skills are treated as exemplar-backed contracts. They are not a complete
low-code authoring product yet.

### Evaluation

Evaluation is a top-level console surface, separate from Observability.

Implemented pieces:

- MLflow GenAI dataset sync via `scripts/sync_mlflow_eval_datasets.py`.
- Deterministic scorer runs via `scripts/run_evaluation_scorers.py`.
- Live trace sampling via `scripts/sample_live_evaluation_traces.py`.
- UC tables:
  - `evaluation_datasets`
  - `evaluation_events`
- Evaluation UI with:
  - high-level health summary;
  - category pass/warning/fail/not-scored decisions;
  - reason tooltips;
  - live quality denominators;
  - recent events;
  - MLflow run/trace links where available.

Current scoring coverage:

- `capability_graph`: joins curated records to `rag_search` events.
- `hipporag2_retrieval`: joins curated records to `rag_answer` events.
- `workspace_context`, `usecase_lifecycle`, `skill_execution`,
  `platform_cost`: registered evalsets exist, but business-quality runtime
  scorers are still incomplete.

### Observability

Observability is for runtime/platform telemetry, model invocation visibility, and
operational health. It should not be confused with Evaluation, which answers
whether BrickVision output quality is good enough to trust.

## Local Development

The most direct local path is the SPA + FastAPI launcher:

```bash
cp .env.example .env
# fill Databricks host/token/warehouse/catalog/schema values

bash scripts/local_deploy/start_local_spa.sh
```

This starts:

- FastAPI sidecar on `127.0.0.1:8000`
- Vite console on `127.0.0.1:5173`

The launcher sources `.env`. Restart it after changing env vars.

Important local env vars:

- `DATABRICKS_HOST`
- `DATABRICKS_TOKEN`
- `DATABRICKS_WAREHOUSE_ID`
- `BV_CATALOG`
- `BV_SCHEMA`
- `BV_VS_ENDPOINT`
- `BV_LAKEBASE_PROJECT_ID`
- `BV_LAKEBASE_BRANCH`
- `BV_LAKEBASE_DATABASE`
- `BV_MLFLOW_EVALUATION_EXPERIMENT_ID` for MLflow eval/tracing

For evaluation scripts, use a virtual environment with:

- `databricks-sdk`
- `mlflow>=3.0`
- `databricks-agents`

If direct PyPI is unavailable, use the Databricks PyPI proxy.

## Databricks Deployment

`databricks.yml` is the deployment source of truth for the serverless jobs. It
also contains a minimal console App stanza, but the current local deploy path
strips the App block and runs the console as local Vite + FastAPI.

Key resources:

- `bv_capability_indexer`: 14-task serverless job that builds and promotes
  Capability Graph snapshots.
- `bv_workspace_context_refresh`: refreshes workspace claims and publishes
  Lakebase synced tables.
- `bv_evaluation_scorers`: syncs eval datasets, runs scorer gates, and samples
  live RAG traces.
- `brickvision_console`: minimal App bundle entry; not yet the normal local
  operator path.

The indexer uses a UC Volume for per-run JSON handoffs. Durable graph state lives
in Delta tables and synced Lakebase tables, not in that volume.

## Repository Layout

```text
apps/console/                       React console
apps/console-api/                   FastAPI sidecar for console reads/actions

src/brickvision/                    CLI and install/preflight code
src/brickvision_runtime/            Runtime libraries and Databricks job code
  capability_graph/                 Graph source adapters, builder, persistence, retrieval
  databricks_jobs/                  Job entrypoints
  kg/                               Retrieval/extraction helpers
  ml/                               ML backend probes and drivers

skills/                             Hand-authored skill contracts and implementations
config/evaluation/                  Evaluation manifests and JSONL evalsets
scripts/                            Local deploy, evaluation, validation, utility scripts
docs/                               Architecture notes; some sections are ahead of code
tests/                              Unit tests and fixtures
```

## What This Is Not

- Not a generic agent framework.
- Not a finished autonomous build/deploy system.
- Not a multi-tenant SaaS control plane.
- Not safe to point at production workspaces without reviewing scope, grants,
  workspace profile config, and write-capable skills.
- Not fully evaluated across all workflow categories yet.
- Not free of local/development artifacts; keep generated files out of commits.

## Known Gaps

- Runtime scoring is strongest for retrieval and weaker for workspace/usecase/skill
  categories.
- Live eval numbers require enough real traffic. A `1/1` trace-backed sample is
  plumbing validation, not product quality.
- Some local paths still depend on process-level Databricks tokens. The desired
  OBO/security model is stricter than the local dev path.
- Lakebase, Vector Search, MLflow, UC permissions, and serverless SQL warehouse
  setup are real prerequisites, not optional polish.
- Historical docs and some sidecar README content still reference retired build
  routes; treat current code and `databricks.yml` as the truth.
- API naming is still uneven: many non-Knowledge operations live under
  `/api/knowledge/*`.

## Key Docs

- [`docs/23-databricks-capability-graph.md`](./docs/23-databricks-capability-graph.md)
- [`docs/24-agent-operating-model.md`](./docs/24-agent-operating-model.md)
- [`docs/19-local-development.md`](./docs/19-local-development.md)
- [`config/evaluation/README.md`](./config/evaluation/README.md)
- [`skills/README.md`](./skills/README.md)

## License

Internal Databricks PS / SI tool. Not for redistribution.
