# `scripts/local_deploy/` — Local-mode BrickVision

Stand up BrickVision **end-to-end on your laptop** against a real
Databricks workspace, without deploying the SPA as a Databricks App.

```text
┌────────────────────────────┐         ┌──────────────────────────────┐
│ Your laptop                │   HTTPS │ Databricks workspace          │
│  • Vite SPA   :5173        │ ◀─────▶ │  • bv_indexer_sp / bv_app_sp  │
│  • Uvicorn    :8000        │         │  • <bv>.<schema>.* (Delta)    │
│                            │         │  • <bv>.<schema>.indexer-state│
│  (start_local_spa.sh)      │         │       (UC Volume; inter-task  │
│                            │         │        JSON hand-off only)    │
│                            │         │  • bv_vs_endpoint + 3 indexes │
│                            │         │  • bv_capability_indexer Job  │
│                            │         │  • bv_evaluation_scorers Job  │
└────────────────────────────┘         └──────────────────────────────┘
```

## 5-minute runbook

```bash
# 1. Once: copy the env template and fill in workspace credentials
cp .env.example .env
$EDITOR .env                                 # set DATABRICKS_HOST + DATABRICKS_TOKEN

# 2. One-shot workspace bootstrap (idempotent; ~12-15 min cold)
bash scripts/local_deploy.sh

# 3. In a separate terminal: start the local SPA + sidecar
bash scripts/local_deploy/start_local_spa.sh

# 4. Click around
open http://localhost:5173/knowledge          # real workspace data
open http://localhost:5173/builds             # local build catalogue
open http://localhost:5173/                    # Visual Builder
```

## Prerequisites

| What | Why | Where to set |
| --- | --- | --- |
| Databricks **workspace admin** + **metastore admin** PAT | SCIM SP creation, `CREATE CATALOG`, `CREATE SCHEMA`, VS endpoint create | `DATABRICKS_HOST` + `DATABRICKS_TOKEN` in `.env` |
| Python 3.11+ | Provisioner + indexer + sidecar | `python3 --version` |
| Databricks CLI ≥ 0.230 | `databricks bundle deploy` | `databricks --version` |
| Node 20+ (`pnpm` or `npm`) | Vite SPA dev server | `node --version` |
| `uvicorn` + `fastapi` | Sidecar | `pip install uvicorn fastapi` |
| `databricks-sdk` ≥ 0.68 | Provisioner SDK calls | `pip install 'databricks-sdk>=0.68'` |
| `PyYAML` ≥ 6.0 | Slim DAB rewriter | `pip install 'PyYAML>=6.0'` |

> If any of those are missing, the bash script will tell you exactly
> what to install. Run `bash scripts/local_deploy.sh --doctor` for a
> read-only checklist.

## What `local_deploy.sh` does

Each phase is **idempotent** — you can re-run after a partial failure
and it will pick up where it left off.

| Phase | Action | Duration |
| ----- | ------ | -------- |
| 0     | Preflight: verify CLIs, .env, auth | ~2 s |
| 1.sp  | SCIM-create `bv_indexer_sp` + `bv_app_sp` | ~5 s |
| 1.whse| Reuse / create the `bv-warehouse` SQL warehouse | ~2 s (~30 s if creating) |
| 1.uc  | `CREATE CATALOG` + 1 schema + indexer-state Volume (`BV_INDEXER_STATE_VOLUME`) | ~5 s |
| 1.ddl | Apply 13 capability-graph Delta tables | ~30 s |
| 1.budget | Seed `<bv>.config.budget_namespaces` (app + indexer rows) | ~5 s |
| 1.grant | Grant SELECT on schema to `bv_app_sp`; ALL_PRIVILEGES to `bv_indexer_sp` | ~5 s |
| 1.vs  | Create `bv_vs_endpoint` + 3 Delta-Sync indexes (cold create blocks ~10 min) | **~10-15 min** |
| 2     | `brickvision install` (8 pre-flights, including N180 capability-graph probes) | ~30 s |
| 3     | Deploy managed Jobs: `bv_capability_indexer`, `bv_workspace_kg_refresh`, `bv_evaluation_scorers` (no Apps) | ~30 s |
| 4     | `brickvision indexer refresh` (first end-to-end run) | **~15-30 min** |
| 5     | Print operator next steps | ~1 s |

## Common knobs

Set in `.env`:

```bash
# Skip an entire phase
BV_LOCAL_DEPLOY_AUTO_PROVISION_SPS=false      # SPs already exist
BV_LOCAL_DEPLOY_AUTO_PROVISION_VS=false       # endpoint already up
BV_LOCAL_DEPLOY_DEPLOY_INDEXER_JOB=false      # already deployed
BV_LOCAL_DEPLOY_TRIGGER_FIRST_REFRESH=false   # inspect before triggering

# Override resource names
BV_CATALOG=brickvision_dev
BV_VS_ENDPOINT=brickvision-dev
BV_LOCAL_DEPLOY_INDEXER_SP_NAME=bv_indexer_sp
BV_LOCAL_DEPLOY_APP_SP_NAME=bv_app_sp

# Tune timeouts
BV_LOCAL_DEPLOY_VS_ENDPOINT_TIMEOUT_SEC=900   # ~10 min cold-create cap
BV_LOCAL_DEPLOY_INDEXER_TIMEOUT_SEC=2400      # ~40 min first-refresh cap
```

Or pass flags to `local_deploy.sh`:

```bash
bash scripts/local_deploy.sh --doctor          # read-only checklist
bash scripts/local_deploy.sh --skip vs         # skip VS phase only
bash scripts/local_deploy.sh --no-trigger      # provision + deploy, no first refresh
bash scripts/local_deploy.sh --resume          # idempotent retry after partial failure
```

## Troubleshooting matrix

| Symptom | Root cause | Fix |
| ------- | ---------- | --- |
| `✗ DATABRICKS_HOST and DATABRICKS_TOKEN must be set in .env` | Missing creds | Copy `.env.example` to `.env`, fill in `DATABRICKS_HOST` (e.g. `https://your-workspace.cloud.databricks.com`) and a workspace-admin PAT |
| `✗ Statement failed (state='FAILED')` on `CREATE CATALOG` | Token lacks metastore admin role | Ask your account admin to grant your user the **metastore admin** role, or use an admin's PAT for the run |
| `✗ Failed to list VS endpoints: PERMISSION_DENIED` | Token lacks `vectorsearch:endpoints.list` | Add the **Workspace admin** role; this also unlocks endpoint creation |
| VS endpoint stuck `PROVISIONING > 15 min` | First-time cold-create on the workspace | Re-run `bash scripts/local_deploy.sh --resume` — the script polls until ONLINE and then continues |
| `databricks bundle deploy` errors with `path not found: ../src/...` | Running from wrong cwd | The script always cd's to repo root; if you ran the Python module directly, do so from repo root: `cd brickvision && python3 -m scripts.local_deploy.deploy_indexer_job` |
| First indexer refresh fails on `embed` task | FMS endpoint missing or low quota | `python3 -m brickvision.cli install --target prod` — the `fms_endpoint_present` pre-flight will say which endpoint to provision; default is `LLM_EMBEDDING_TASKS` |
| First indexer refresh fails on `vs_upsert` | VS index creation lagged behind endpoint ONLINE | Wait ~5 min, then `python3 -m brickvision.cli indexer refresh` to retry; index sync is async |
| `port 5173 already in use` when starting SPA | Another Vite dev server is running | `BV_LOCAL_SPA_PORT=5174 bash scripts/local_deploy/start_local_spa.sh` |
| `/knowledge` tab shows "indexer has not yet run" banner | Indexer triggered but still running | `python3 -m brickvision.cli indexer status` to check progress; banner clears when `promote` task succeeds and `<bv>.capability_graph.active_snapshot_id` is populated |
| Evaluation page has datasets but no quality gates | Scorer Job has not run yet | `python3 -m brickvision.cli evaluation run --existing-data`, then `python3 -m brickvision.cli evaluation status` |

## Logs

All output is teed to `./local_deploy.log` (configurable via
`BV_LOCAL_DEPLOY_LOG_PATH`). Re-runs append, never truncate, so you
have a full history of every operator action.

## What this *doesn't* do

- **Doesn't deploy the BrickVision Visual Builder as a Databricks App.**
  The `apps:` block in `databricks.yml` is stripped from the slim DAB
  on every deploy. The SPA runs locally only.
- **Doesn't auto-provision an FMS endpoint.** Your workspace must
  already have the endpoint named in `LLM_EMBEDDING_TASKS`. The install pre-flight will tell
  you if it's missing.
- **Doesn't auto-grant cross-workspace OAuth secrets** for the
  service principals. The indexer Job runs under `bv_indexer_sp` via
  Databricks' built-in `run_as.service_principal_name` — no secrets
  required for in-workspace runs. If you also want to call the SPs
  from outside the workspace, generate OAuth secrets manually via
  the Account Console.
- **Doesn't seed example skills.** The capability indexer pulls
  exclusively from the Databricks SDK + REST API + docs — no manual
  seeds. The first refresh produces the entire snapshot from
  upstream sources.

## Discipline notes

- All Python helpers in this directory are import-cheap (no top-level
  `databricks-sdk` import) so the test suite can verify their
  config-loading behaviour without a workspace.
- Every external call goes through a real `databricks.sdk.WorkspaceClient`
  — there are **no Protocol seams, no mock classes** (rule 15).
  `BV_DRY_RUN=true` short-circuits writes for inspection runs but
  still uses the real SDK to read state.
- The slim DAB written by `deploy_indexer_job.py` is regenerated
  on every run from the canonical `databricks.yml`; the source of
  truth stays singular.
- The deploy helper uploads `config/evaluation/*` plus the evaluation
  scripts into the same workspace source root and rewrites
  `bv_evaluation_scorers` to read those workspace files.
