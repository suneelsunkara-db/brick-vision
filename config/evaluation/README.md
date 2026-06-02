# BrickVision Evaluation Sets

BrickVision evaluation records live outside application code and are synced into
MLflow GenAI evaluation datasets backed by Unity Catalog.

Create `evalsets.json` in this directory from `evalsets.example.json`, then add
one JSONL file per dataset. Each JSONL line must follow the MLflow evaluation
dataset contract:

```json
{"inputs":{"question":"..."},"expectations":{"expected_facts":["..."],"expected_retrieved_context":[{"doc_uri":"..."}]},"source":{"human":{"user_name":"expert@databricks.com"}},"tags":{"workflow":"hipporag2_retrieval"}}
```

Use these dataset families for BrickVision workflows:

- `capability_graph`
- `hipporag2_retrieval`
- `workspace_context`
- `usecase_lifecycle`
- `skill_execution`
- `platform_cost`

Retrieval evalsets must target the active BrickVision corpus they score. For
the live Capability Graph `entity_index`, expected contexts should reference
active snapshot IDs such as `docs:aws:...` chunks or extension IDs that exist in
`<BV_CATALOG>.<BV_SCHEMA>.extensions`. Do not use retired fixture-only gold IDs
or BrickVision repository source paths unless that source corpus has been
indexed into the evaluation target.

Sync curated records with:

```bash
python scripts/sync_mlflow_eval_datasets.py --manifest config/evaluation/evalsets.json
python -m brickvision.cli evaluation sync-datasets --manifest config/evaluation/evalsets.json
```

The sync script creates or updates MLflow GenAI datasets, merges records with
`mlflow.genai.datasets`, and registers dataset metadata in
`<BV_CATALOG>.<BV_SCHEMA>.evaluation_datasets` for the Console Evaluation page.

Run scorer coverage and runtime-event checks with:

```bash
python scripts/run_evaluation_scorers.py --manifest config/evaluation/evalsets.json --dry-run
python scripts/run_evaluation_scorers.py --manifest config/evaluation/evalsets.json
python -m brickvision.cli evaluation run --existing-data
python -m brickvision.cli evaluation status
```

The Databricks Asset Bundle defines a scheduled serverless Job,
`bv_evaluation_scorers`, which runs the same scorer script daily. The runner
first validates dataset coverage, then, when warehouse access is available,
compares recent `evaluation_events` against dataset expectations for
Capability Graph and HippoRAG2 retrieval workflows. The same Job also runs the
live trace sampler after scorer gates so each daily run materializes the latest
trace-backed HippoRAG2 sample dataset.

The scorer runner now has two evaluation modes:

- **Default deterministic mode**: validates dataset shape, applies manifest
  gates, joins retrieval records to recent UC `evaluation_events`, and writes one
  `scorer_run` event per dataset. This mode is used by the scheduled Job and
  exits non-zero when any workflow gate fails.
- **Opt-in MLflow GenAI Agent Evaluation mode**: pass
  `--mlflow-genai-evaluate` or set `BV_EVALUATION_USE_MLFLOW_GENAI=true` to run
  `mlflow.genai.evaluate(...)` over runtime-matched retrieval records and persist
  the returned MLflow run id on the `scorer_run` event. This requires MLflow 3
  GenAI scorers to be available in the Databricks runtime and may invoke judge
  models.

Live quality uses a separate trace-sampled path. It does not replace curated
regression datasets:

```bash
python scripts/sample_live_evaluation_traces.py --workflow hipporag2_retrieval --event-kind rag_answer --hours 24 --limit 500 --dry-run
python scripts/sample_live_evaluation_traces.py --workflow hipporag2_retrieval --event-kind rag_answer --hours 24 --limit 500
```

The sampler reads recent rows from
`<BV_CATALOG>.<BV_SCHEMA>.evaluation_events`, requires `mlflow_trace_id` by
default, creates a daily MLflow GenAI dataset named like
`<BV_CATALOG>.<BV_SCHEMA>.bv_eval_live_hipporag2_retrieval_rag_answer_YYYYMMDD`,
and registers it in `evaluation_datasets` with
`brickvision_dataset_source=live_trace_sample`. These records have real inputs,
outputs, evidence, status, reason codes, and trace links. They usually do not
have human ground truth, so they are scored with judge-style checks and
population metrics, not strict gold-set pass/fail gates.

The Evaluation UI therefore has two quality views:

- **Regression gates**: curated JSONL records with stable expectations, useful
  for detecting known regressions.
- **Live quality**: last-24-hour denominators from `evaluation_events`, useful
  for seeing real traffic volume, success rate, trace coverage, failures, and
  latency.

Live local sync prerequisites:

- Install the evaluation client dependencies into an isolated environment:
  `mlflow>=3.0`, `databricks-agents`, and `databricks-sdk>=0.68`.
- Set `BV_MLFLOW_EVALUATION_EXPERIMENT_ID` to a Databricks MLflow experiment id.
  For example, create or reuse `/Shared/brickvision-evaluation` and pass that id
  when running the sync/scorer commands.
- Use the Databricks PyPI proxy when direct PyPI access is unavailable:
  `--index-url https://pypi-proxy.cloud.databricks.com/simple`.
- If the local `mlflow.genai.datasets` client tries to sync UC dataset records
  and raises a missing `pyspark` error, install `databricks-connect` only in the
  local validation environment. Do not add it to the deployed Databricks
  serverless evaluation Job; the Job runtime should use Databricks-managed
  dependencies (`databricks-sdk`, `mlflow`, `databricks-agents`) only.

Live validation should use a workspace-local MLflow experiment such as
`/Shared/brickvision-evaluation`. Do not commit real experiment ids, run ids, or
workspace catalog names. In the validation run used for this repo update, all six
datasets synced, deterministic scorer gates passed, and the HippoRAG2 opt-in
MLflow Agent Evaluation path completed.

Knowledge Ask now logs a best-effort MLflow trace when
`BV_MLFLOW_EVALUATION_EXPERIMENT_ID` is configured and the Console API runtime
has `mlflow-skinny>=3.0` installed. The emitted `rag_answer` event stores the
trace id in `mlflow_trace_id`, and the Evaluation page links it back to the
Databricks experiment trace view.

For local deploy, `scripts/local_deploy/deploy_indexer_job.py` uploads the
evaluation scripts and JSONL manifests to the operator's workspace source root
and rewrites the Job payload so `bv_evaluation_scorers` reads those workspace
files. Use `brickvision evaluation run` to trigger the deployed Job manually.

`--existing-data` is the normal operator path after the capability indexer and
Workspace KG jobs have already run. It triggers only `bv_evaluation_scorers` and
does not refresh upstream jobs; the scorers read the registered MLflow datasets
and the existing `<BV_CATALOG>.<BV_SCHEMA>.evaluation_events` table.

## Current scoring coverage

Implemented runtime gates:

- `capability_graph`: joins records to latest `rag_search` events by query hash;
  enforces top-1 hit rate and expected-context recall when configured.
- `hipporag2_retrieval`: joins records to latest `rag_answer` events by query
  hash; enforces document recall and grounded-answer rate when configured.

Dataset-readiness only today:

- `workspace_context`
- `usecase_lifecycle`
- `skill_execution`
- `platform_cost`

These four families have JSONL records and manifest gates, but their runtime
emitters/scorers are not complete enough to enforce business-quality thresholds
yet.

Known limits:

- `mlflow_trace_id` is populated for Knowledge Ask (`rag_answer`) when MLflow
  tracing is configured. Search, usecase, tool-proof, indexer, workspace-refresh,
  and platform-cost emitters still leave trace ids empty.
- The Console reads latest `scorer_run` events from
  `<BV_CATALOG>.<BV_SCHEMA>.evaluation_events`; there is no
  `evaluation_run_summaries` table yet.
- Runtime scoring requires matching Search/Ask traffic before `--existing-data`
  can produce meaningful retrieval metrics; otherwise the runner emits
  `EVAL_RUNTIME_EVENTS_MISSING`.
- MLflow GenAI Agent Evaluation is available as an opt-in pilot path, not the
  default scheduled gate.
