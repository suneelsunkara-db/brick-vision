# 25 · BrickVision Direction Review

**Status:** deep implementation review, May 2026  
**Scope:** backend/runtime, Console UI, PRD/docs, Lakebridge migration direction, model endpoint policy, and product information architecture.  
**Review posture:** identify concrete gaps between implemented code and the intended BrickVision direction, then recommend a focused path forward.

---

## Executive Summary

BrickVision is currently strongest as a **Databricks-first usecase proof console**:

- It can index Databricks capabilities into a Capability Graph.
- It can show live Databricks Capability Graph and Workspace Context surfaces.
- It can create usecase records and run proof families.
- It can exercise SQL, PySpark, ML planning/training, Lakebridge SQL Transpile, and Lakebridge Switch Code Convert paths.
- It now uses only two model endpoint classes:
  - `LLM_GENERAL_TASKS=databricks-qwen3-next-80b-a3b-instruct`
  - `LLM_EMBEDDING_TASKS=databricks-qwen3-embedding-0-6b`

BrickVision is **not yet** a full autonomous agent operating system. The code does not yet implement the durable five-stage agent lifecycle described in the PRD:

```text
stage:agent-design -> stage:agent-generate -> stage:agent-validate -> stage:agent-evaluate -> stage:agent-productionize
```

The immediate product direction should be:

> Make BrickVision a reliable, partner-facing Databricks proof console centered on usecases, governed workspace evidence, contracted skills, durable execution state, and evaluation gates. Defer the full autonomous agent OS until durable orchestration, Skill Library build/import lifecycle, profile enforcement, and live artifact execution are in place.

---

## Direction Decision

### What BrickVision Should Be Now

BrickVision should be a **usecase-centric Databricks partner console**.

The console should answer:

1. What does this workspace contain?
2. What business usecases are plausible from that evidence?
3. What skills and Databricks capabilities are required?
4. Which proof runs succeeded, failed, or are blocked?
5. What artifacts were produced?
6. What evaluation or governance gates remain?

### What BrickVision Should Not Claim Yet

BrickVision should not yet present itself as:

- **A full autonomous agent OS on Databricks**: the durable Databricks-backed stage lifecycle for design, generate, validate, evaluate, and productionize does not exist yet.
- **A complete Skill Library build/import system on Databricks**: BrickVision can inspect skill contracts and draft some readiness skills, but cannot yet persist Skill Requests, coordinate Skill Builds, validate/sign/package partner skills, or publish skill lifecycle facts into a Skill Knowledge Graph.
- **A real-time Shared Context Graph for agent collaboration**: the target design is now clear, but BrickVision has not yet implemented Lakebase-backed context events, current graph projections, live Skill Build state, or reasoning-memory UI lenses.
- **A production deployment orchestrator for Databricks workloads**: BrickVision can prove artifacts and submit some jobs, but does not yet manage full deploy, serve, rollback, approval, and promotion lifecycles.
- **A full Lakebridge migration factory on Databricks**: SQL Transpile and PySpark Code Convert slices exist, but the full analyzer -> converter/transpiler -> validate/reconcile lifecycle is not wired end-to-end.
- **A general Databricks control plane UI**: the product should stay focused on BrickVision usecases, skills, proofs, artifact bundles, evaluation, and observability.

Those are plausible future directions, but claiming them before the durable orchestration and authoring pieces exist will make the product feel inconsistent.

### Current Architecture Decision

Skill Builds with Agents should use a real Shared Context Graph with three layers:

- **Long-Term Memory**: Databricks Capability Graph, Skill Knowledge Graph, and Workspace Context Graph.
- **Shared Working Memory**: active Skill Requests, Skill Build Runs, agent tasks, draft artifacts, and evidence packs.
- **Reasoning Memory**: decision traces, rejected alternatives, reviewer findings, and trust promotions.

Lakebase is the live operational store for this graph. UC Volumes hold large draft/build artifacts. Approved core skills remain in `skills/`; approved partner skills belong in `skill-packs/<partner>/`; Vector Search is used for semantic retrieval over curated summaries.

---

## Model Endpoint Policy

### Current Decision

All model endpoint configuration should collapse to two endpoint classes:

| Endpoint class | Purpose | Default |
|---|---|---|
| `LLM_GENERAL_TASKS` | All non-embedding LLM work: KG extraction, Knowledge answers, code generation, migration Code Convert, evaluation judging, skill planning | `databricks-qwen3-next-80b-a3b-instruct` |
| `LLM_EMBEDDING_TASKS` | All embedding work for Capability Graph / Vector Search | `databricks-qwen3-embedding-0-6b` |

### Problem Found

The previous design used many local endpoint knobs:

- `BV_KG_EXTRACTOR_ENDPOINT`
- `BV_MODEL_ROLE_KG_ANSWER`
- `BV_MODEL_ROLE_SQL_CODEGEN`
- `BV_MODEL_ROLE_PYSPARK_CODEGEN`
- `BV_MODEL_ROLE_ML_CODEGEN`
- `BV_MODEL_ROLE_SKILL_RUNTIME_DEFAULT`
- `BV_SWITCH_MODEL_ENDPOINT`
- `DATABRICKS_MODEL_SERVING_ENDPOINT`
- legacy embedding defaults such as `databricks-gte-large-en`

This created model routing drift. Different code paths could silently pick different LLMs for similar work.

### Why It Matters

For a partner-facing product, model routing must be explainable:

- Operators should know which endpoint is used for non-embedding tasks.
- Embedding should be isolated because it has different API shape, cost, dimensions, and caching behavior.
- Observability should group usage by endpoint class, not by a growing list of symbolic roles.

### Recommendation

Keep only:

```text
LLM_GENERAL_TASKS
LLM_EMBEDDING_TASKS
```

If production later needs per-role routing, reintroduce it only through an auditable UC table, not through local `.env` proliferation.

---

## Lakebridge Direction

### Current Decision

Lakebridge must be split into distinct product families:

| Family | User-facing label | Purpose |
|---|---|---|
| `Migration Assessment` | Analyze | Source-system and artifact analysis: estate shape, dependencies, lineage, complexity, and readiness |
| `Migration` | SQL Transpile | SQL converter/transpiler for source SQL into Databricks SQL plus remediation |
| `Code Convert` | Code Convert | Code converter/transpiler for legacy PySpark using Lakebridge Switch |
| `Data Reconcile` | Reconcile | Source-target data reconciliation after migration or conversion |

### Problem Found

Earlier implementation and UI copy risked blending:

- SQL transpilation
- legacy PySpark code conversion
- source assessment
- reconciliation
- profiling
- migration validation

These are not the same workflow. They have different inputs, outputs, tools, and readiness gates.

### Why It Matters

Partners need to understand exactly what BrickVision ran:

- Assessment / Analyzer should identify source systems, source artifacts, lineage, complexity, and migration blockers.
- Converter / Transpiler should transform SQL or code into Databricks artifacts.
- Validate / Reconcile should depend on artifact type:
  - **Data migration**: reconcile source vs target data.
  - **SQL transpilation**: validate generated SQL with parse, EXPLAIN, execution, row/count checks, and remediation findings.
  - **Code conversion**: validate converted code artifacts with parse/import checks, notebook execution smoke tests, dependency checks, and Switch diagnostics.

Assessment and Data Reconcile should stay candidate surfaces until tested end-to-end.

### Current Status

Code Convert now works through Lakebridge Switch:

1. Source legacy PySpark lives in a UC Volume.
2. BrickVision copies it to local staging for Switch CLI input.
3. Switch submits a Databricks job.
4. Switch writes intermediate Workspace output.
5. BrickVision exports output and copies artifacts back to a UC Volume.

The recent error:

```text
Export error: The parent folder (...) does not exist.
```

was addressed by adding a `prepare_workspace_output` stage that runs:

```text
databricks workspace mkdirs <workspace_output_folder>
```

before the Switch job is submitted.

### Current Status

SQL Transpile now runs Lakebridge live from a UC Volume source path. If no source is entered in the UI, BrickVision derives the seeded SQL sample path from the existing catalog/schema/volume settings and still executes the live Lakebridge CLI path.

### Recommendation

Keep user-facing migration states explicit:

1. **Live transpile**: runs Lakebridge SQL transpilation against UC Volume or inline source SQL.
2. **Live convert**: runs Lakebridge Switch against UC Volume source code.
3. **Validate / reconcile**: remains a separate follow-on action by artifact type.

Checked-in proof artifacts can remain developer fixtures, but they should not be a user-facing execution mode.

---

## Backend And Runtime Review

### 1. Missing Durable Agent Lifecycle

**Severity:** Critical  
**Area:** orchestration  
**Relevant modules:** `usecase_records.py`, `usecase_planner.py`, `usecase_executions.py`, `skill_execution_service.py`

#### Finding

The PRD describes a five-stage agent lifecycle:

```text
design -> generate -> validate -> evaluate -> productionize
```

The runtime does not yet implement that lifecycle as a durable orchestrator. Current execution is family-oriented and proof-oriented:

- SQL
- PySpark
- ML
- Migration / SQL Transpile
- Code Convert
- AI

#### Impact

The product can run useful proofs, but it cannot honestly claim to be an autonomous multi-stage agent system. There is no single persisted execution DAG that captures the staged lifecycle.

#### Recommendation

Create a minimal `usecase_orchestrator.py` that persists:

- execution plan
- stage state
- skill invocations
- validation results
- evaluation results
- artifact outputs
- approval gates

Start with one vertical slice:

```text
Usecase -> Strategy -> SQL Transpile or Code Convert -> Validate -> Artifact Proof
```

Do not implement all five stages at once.

---

### 2. API Surface Collapsed Under Knowledge

**Severity:** Critical  
**Area:** API architecture  
**Relevant module:** `apps/console-api/src/console_api/routers/knowledge.py`

#### Finding

Usecases, workspace context, Skill Library, evaluation, observability, and execution monitor endpoints are grouped under `/api/knowledge`.

#### Impact

This makes product boundaries unclear:

- Evaluation is not Knowledge.
- Observability is not Knowledge.
- Usecase execution is not Knowledge.
- Skill Library is not Knowledge.

It also makes future authorization and auditing harder.

#### Recommendation

Split routers by product surface:

```text
/api/knowledge
/api/workspace
/api/usecases
/api/skills
/api/evaluation
/api/observability
```

This should be done before adding more UI features.

---

### 3. Skill Library Is Mostly Inventory-Only

**Severity:** High  
**Area:** skill lifecycle  
**Relevant modules:** `skill_builder_service.py`, `skill_contracts.py`, `apps/console/src/routes/skill-builder.tsx`

#### Finding

The current skill UI lists checked-in `SKILL.yaml` contracts and runtime readiness. It does not yet persist Skill Requests, coordinate Skill Builds, validate/sign/import partner skills, or publish skill lifecycle facts into a Skill Knowledge Graph.

#### Impact

The product should be called Skill Library because the primary user expectation is “what skills exist and can agents use?” Build/import flows are sections inside the library, not a separate top-level product.

#### Recommendation

Rename the current UI to **Skill Library**.

Expose these library sections as the lifecycle matures:

- Available Skills
- Skill Requests
- Skill Builds
- Draft Skills
- Partner Skills

---

### 4. Workspace Profile Is Not Fully Enforced

**Severity:** High  
**Area:** governance  
**Relevant modules:** `workspace_context_service.py`, `workspace_visibility.py`, `usecase_suggestions.py`, `src/brickvision_runtime/config.py`

#### Finding

Workspace profile YAML exists, but console API paths do not consistently enforce allowed catalogs, blocked catalogs, read-only mode, or partner scoping.

#### Impact

BrickVision can surface or act on workspace evidence outside the intended partner profile. That undermines governance.

#### Recommendation

Every workspace-scoped API should load the active workspace profile and apply:

- allowed catalog filter
- blocked catalog filter
- read-only policy
- volume/write target policy
- model endpoint policy

---

### 5. Execution Monitor State Is Ephemeral

**Severity:** High  
**Area:** reliability  
**Relevant module:** `usecase_executions.py`

#### Finding

Execution monitor state is held in process memory.

#### Impact

When the sidecar restarts:

- execution history can disappear
- polling loses state
- long-running Databricks jobs outlive the local process
- the UI cannot reliably reconnect

#### Recommendation

Persist execution runs in UC:

```text
workspace_usecase_executions
workspace_usecase_execution_steps
workspace_usecase_execution_artifacts
```

The local memory cache can remain a fast view, but not the source of truth.

---

### 6. SQL Transpile Must Stay Live

**Severity:** High  
**Area:** migration proofing  
**Relevant module:** `skill_execution_service.py`

#### Finding

SQL Transpile previously returned a bundle from `proof-artifacts/lakebridge`, which risked making a known fixture look like a live migration proof.

#### Impact

The UI must not imply that a replayed fixture is a live migration proof. Partner-facing actions should run against bound or derived live inputs.

#### Recommendation

Add explicit proof mode:

```text
proof_mode: live_transpile | live_convert | manual_upload
```

All user-facing actions should run live. Checked-in artifacts can remain as
developer fixtures, but they should not be the default execution path.

---

### 7. ML Proofing Is Partial

**Severity:** Medium  
**Area:** ML execution  
**Relevant module:** `skill_execution_service.py`

#### Finding

ML execution has training/register proofing, but not a full train -> register -> alias -> serve -> evaluate lifecycle.

#### Impact

The UI may suggest ML execution is complete when it only proves part of the path.

#### Recommendation

Split ML status into clear phases:

- data readiness
- training submitted
- training complete
- model registered
- alias assigned
- endpoint deployed
- evaluation passed

---

### 8. Budget And Model Invocation Telemetry Is Incomplete

**Severity:** Medium  
**Area:** observability  
**Relevant modules:** `observability_service.py`, `capability_graph/llm.py`, `embed.py`

#### Finding

Observability shows readiness and configuration, but does not yet persist per-model call ledger rows.

#### Impact

Users cannot answer:

- Which endpoint was called?
- How many tokens were used?
- Which usecase or skill caused the call?
- What did it cost?
- Did it fail?

#### Recommendation

Create:

```text
model_invocation_ledger
```

Minimum columns:

- invocation_id
- namespace
- usecase_id
- skill_id
- endpoint_class
- endpoint_name
- input_tokens
- output_tokens
- latency_ms
- status
- error_kind
- created_at_ms

---

## UI Review

### 1. Usecases Page Does Not Resume Saved Work

**Severity:** Critical  
**Area:** information architecture  
**Relevant UI:** `routes/usecases.tsx`

#### Finding

The Usecases page primarily lists proposals/candidates. It does not present a clear list of persisted usecases in progress.

#### Impact

Users can create work but cannot easily return to it. This breaks the core product loop.

#### Recommendation

Usecases should have tabs:

```text
Proposals | In Progress | Ready | Archived
```

Make Usecases the default home.

---

### 2. Usecase Detail Is Too Much Vertical Scroll

**Severity:** Critical  
**Area:** workflow design  
**Relevant UI:** `routes/usecases.$usecaseId.tsx`

#### Finding

Usecase detail is a long stacked page rather than a guided stepper.

#### Impact

Users cannot tell where they are in the lifecycle. Deploy, evaluation, proofing, and artifact validation blur together.

#### Recommendation

Adopt tabs or stepper:

```text
Outcome | Evidence | Strategy | Skills | Plan | Run | Validate | Evaluate | Deploy | Proof
```

Only show one major step at a time.

---

### 3. Execution Is Split Across Detail And Monitor

**Severity:** High  
**Area:** user flow  
**Relevant UI:** `usecases.$usecaseId.tsx`, `usecases.$usecaseId.executions.tsx`

#### Finding

Some proof actions live on usecase detail. Rich execution monitoring lives on another route.

#### Impact

Users do not know where to run or inspect work.

#### Recommendation

One flow:

```text
Usecase Detail -> Run & Monitor
```

The monitor should cover all families:

- SQL
- PySpark
- ML
- SQL Transpile
- Code Convert
- AI

---

### 4. Code Convert And SQL Transpile Are Not First-Class Everywhere

**Severity:** High  
**Area:** migration UX  
**Relevant UI:** execution monitor and usecase detail routes

#### Finding

The execution monitor handles Code Convert and SQL Transpile better than the usecase detail page.

#### Impact

Migration feels bolted on rather than a core usecase path.

#### Recommendation

Add a Migration step that clearly separates:

```text
SQL Transpile
Code Convert
Assessment / Reconcile (not ready)
```

Do not hide Code Convert behind generic Migration.

---

### 5. Skill UI Naming Must Use Library Language

**Severity:** High  
**Area:** product naming  
**Relevant UI:** `routes/skill-builder.tsx`

#### Finding

The page started as a read-only contract inventory, but the product direction now includes in-house custom skill builds and future partner skill imports.

#### Recommendation

Use **Skill Library** as the route/product label. Treat building as a lifecycle section: Skill Requests -> Skill Builds -> Draft Skills -> Published Available Skills.

---

### 6. Scaffold UI Leaks Into Product

**Severity:** High  
**Area:** product trust  
**Relevant UI:** usecases/workspace/execution monitor routes

#### Finding

There are wireframes, dev notes, disabled buttons, and internal architecture notes in user-facing pages.

#### Impact

It makes working features feel unfinished.

#### Recommendation

Remove or hide:

- wireframe preview cards
- “next backend slice” copy
- internal deep review notes
- disabled CTAs without a clear reason

Replace with truthful product empty states.

---

### 7. Evaluation Naming Collision

**Severity:** Medium  
**Area:** information architecture

#### Finding

“Evaluation” means both:

- platform MLflow evaluation
- usecase readiness review

#### Recommendation

Use separate labels:

- **Evaluation**: MLflow/scorer platform quality
- **Readiness Review**: per-usecase go/no-go

---

## Docs And PRD Review

### 1. Version And Status Drift

**Severity:** High

#### Finding

Docs disagree on whether the product is:

- v0.7.7
- v0.7.8
- v0.7.9 proposed
- v0.7.9 implemented

#### Recommendation

Create a `docs/README.md` with:

- current operational version
- active docs
- retired docs
- current implementation status

---

### 2. Dead Cross-Links To Deleted PRDs

**Severity:** High

#### Finding

Surviving docs still reference deleted docs `01` through `21` and deleted `PRD.md`.

#### Impact

The PRD corpus is not navigable.

#### Recommendation

Either:

1. restore a slim `PRD.md`, or
2. make `docs/24-agent-operating-model.md` the canonical product spec and remove dead links.

---

### 3. Product Scope Is Overstated

**Severity:** High

#### Finding

Docs describe full agent orchestration, signed partner skill packs, and deploy automation, but code mostly implements proof execution.

#### Recommendation

Add an “As Built” matrix:

| Surface | Status |
|---|---|
| Capability Graph | live |
| Workspace Context | partial/live |
| Usecases | partial |
| Skill Library / Available Skills | live |
| Skill Requests / Builds / Drafts | not implemented |
| Execution monitor | live, ephemeral |
| Durable orchestrator | not implemented |
| Evaluation | live/partial |
| Observability | live/partial |

---

## Features That Do Not Make Sense In Current Direction

### Keep But Reframe

| Current feature | New framing |
|---|---|
| Skill Builder / Skill Catalog | Skill Library |
| Migration | SQL Transpile |
| Code Convert under Migration | Separate Code Convert family |
| Usecase Evaluation | Readiness Review |
| Knowledge | Databricks Capability Graph + Workspace Context |

### Hide Or Remove

- Wireframe preview blocks.
- Disabled CTAs that do not explain next action.
- Internal “deep review notes” inside product UI.
- Duplicate execution route aliases.
- Static demo matrices next to live backend data.

### Do Not Expand Yet

- Full deployment UX.
- Full partner skill authoring.
- Full Lakebridge assessment/reconcile.
- General AI agent claims.

These should wait until the durable orchestrator and live artifact paths are stable.

---

## Recommended Roadmap

### Phase 1: Make The Current Product Honest

1. Usecases page lists persisted usecases.
2. Skill UI becomes Skill Library.
3. Migration UI separates SQL Transpile and Code Convert.
4. SQL replay proofs are labeled demo replay.
5. Remove scaffold/dev UI panels.

### Phase 2: Durable Execution

1. Persist execution runs in UC.
2. Persist execution steps and artifacts.
3. Make Code Convert reconnectable after sidecar restart.
4. Add Databricks run URL as first-class execution metadata.

### Phase 3: Minimal Stage Orchestrator

1. Implement `stage:agent-design`.
2. Implement `stage:agent-validate`.
3. Wire scorers for SQL and migration first.
4. Delay full productionize/deploy until proof quality is stable.

### Phase 4: Live Migration Proofing

1. SQL Transpile accepts source SQL path from UC Volume.
2. Lakebridge SQL transpile runs live.
3. BrickVision remediation is explicit and auditable.
4. Code Convert exports artifacts to UC Volume and displays result-table diagnostics.

### Phase 5: Observability And Evaluation

1. Add `model_invocation_ledger`.
2. Show usage by endpoint class.
3. Link usecase proof failures to evaluation datasets/scorers.
4. Separate observability from evaluation in API and UI.

---

## Final Assessment

BrickVision is moving in the right direction if it focuses on:

- Databricks evidence.
- Usecase outcomes.
- Contracted skill proofs.
- Artifact bundles.
- Evaluation and governance.
- Clear migration families.
- Simple model endpoint policy.

BrickVision will drift if it keeps adding surfaces before fixing:

- durable execution,
- usecase resume,
- workspace profile enforcement,
- UI information architecture,
- live-vs-demo proof labeling,
- Skill Library honesty,
- API domain boundaries.

The strongest next product move is not adding more skills. It is making the existing proof console durable, navigable, and honest.
