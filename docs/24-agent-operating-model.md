# 24 · Agent Operating Model

**Covers:** §24.0 - §24.12 — how BrickVision agents operate: Workspace Context-driven opportunity discovery, Build Suggestions, the execution lifecycle, YAML-backed configuration bundle model, partner workspace profiles, Skill Library, partner skill packs, skill resolution, state machine, tool registry, input/output wiring, budget enforcement, and approval gates that turn the Databricks Capability Graph + Skill Knowledge Graph + Workspace Context Graph into an autonomous outcome-execution system.

**Related:** [`23-databricks-capability-graph.md`](./23-databricks-capability-graph.md) §23 (the retrieval substrate agents consume), [`19-local-development.md`](./19-local-development.md) (operator bootstrap), [`22-changelog.md`](./22-changelog.md) (release decisions), [`../config/evaluation/README.md`](../config/evaluation/README.md) (evaluation dataset/scorer operations).

**Audience:** Anyone building, evaluating, or operating BrickVision's agent execution layer. Anyone deciding how a new skill should be structured. Anyone debugging a failed agent execution.

**Status:** v0.7.9 (proposed) — partially implemented. The Databricks Capability Graph, serverless Workspace Context refresh, Lakebase-backed Workspace Context UI, Evaluation cockpit, and 27+ core SKILL.yaml exemplars provide the foundation. **NEW v0.7.9 decision:** partner workspace connection profiles and partner skill pack manifests are file-backed YAML configuration bundles, not UC tables. UC tables store observations, audit rows, claims, and run results after BrickVision has already authenticated; they do not bootstrap trust or connection identity. **NEW v0.7.9 direction:** BrickVision must not only answer questions or wait for a user-authored outcome. It must inspect Workspace Context, suggest concrete business opportunities, and turn an accepted opportunity into an executable build through the five-stage agent lifecycle. **NEW v0.7.9 Skill Library decision:** in-house and partner-authored skills are requested, built/imported, reviewed, tested, and published through the Skill Library lifecycle. Published skills become available to Usecase planning and `stage:agent-design` only after validation. **NEW v0.7.9 UI decision:** Workspace Context, Business Usecases, Technical Artifacts, Skill Library, and Evaluation are distinct user surfaces. Evaluation is a MLflow/Unity Catalog-backed quality cockpit, not an Observability tab. A SQL view, PySpark notebook, ML experiment, or AI-agent config is an artifact inside a usecase; it is not the usecase itself.

---

## §24.0 Thesis

BrickVision's agents are NOT chat-based assistants. They are **outcome discoverers and executors**. They inspect the active partner workspace, propose concrete things BrickVision can build, and, once a suggestion is accepted or edited, decompose it into a DAG of typed, contracted skill invocations that execute against the workspace with full auditability.

The architecture has three properties that distinguish it from generic "agent frameworks":

1. **Grounded**: Every action an agent takes is traceable to a specific extension in the Capability Graph. No hallucinated API calls.
2. **Contracted**: Every skill has typed inputs, typed outputs, constitutional constraints, budget caps, and eval scorers declared BEFORE execution. No open-ended tool use.
3. **Accountable**: Every execution produces a signed audit trail that can be replayed, evaluated, and rolled back. No black-box reasoning.
4. **Partner-extensible**: Partners teach BrickVision their proven delivery patterns through Skill Library import/review flows and signed skill packs, not by forking BrickVision core.
5. **Config-separated**: Workspace connection profiles and partner skill packs live in declarative YAML bundles with secret references and content hashes. They are not stored as mutable UC control-plane rows.
6. **Workspace-aware**: Suggestions and plans are grounded in the active Workspace Context. BrickVision should recommend builds because it sees specific tables, functions, volumes, schemas, and governance gaps — not because it has a generic template library.
7. **Usecase-separated**: Evidence starters, business usecase candidates, build strategies, required skills, generated artifacts, and lifecycle stages are separate concepts in both API contracts and UI. BrickVision must not collapse "generated SQL" into "business usecase complete."

---

## §24.0.A Shared Context Graph

BrickVision agents need a shared, structured context they can reason over together. This is not a prompt summary, a table of ids, or a generic graph visualization. It is a **Shared Context Graph** with three memory layers:

1. **Long-Term Memory** — what is already known and reusable:
   - Databricks Capability Graph: what Databricks and Lakebridge can do.
   - Skill Knowledge Graph: what BrickVision in-house skills and partner skills can do.
   - Workspace Context Graph: what exists in the active workspace.
2. **Shared Working Memory** — what is happening right now:
   - Skill Build Runs, agent tasks, evidence packs, draft artifacts, active reviews, and live workflow state.
3. **Reasoning Memory** — why decisions were made:
   - decision traces, rejected alternatives, policy checks, reviewer findings, trust promotions, and blocked/retry reasons.

"Shared" does not mean one global blob every agent sees. It means that, within a given graph, multiple producers — collaborating agents, the human reviewer, and the UI — read and write the same live state. The graph is shared at three scopes, one per memory layer:

- **Long-Term Memory is workspace-wide and durable.** The Capability Graph, Skill Knowledge Graph, and Workspace Context Graph are read by every agent, usecase, and build. This is the broadest scope.
- **Shared Working Memory is per-run.** Each active run (a Skill Build Run, a usecase execution) has its own `graph_id`. The agents on that run, the reviewer, and the UI collaborate on that run's working state in real time. This is the "shared among collaborating agents" scope.
- **Reasoning Memory is per-run, retained.** Decision traces for a run are shared with reviewers and future builds so rejected alternatives and policy findings survive.

The same substrate (events → projections → lenses) serves all three scopes; only the `graph_id` scoping differs (e.g. `graph_id=skill_build:sbr_...`, `graph_id=outcome_exec:...`, or a long-term graph id). The first implementation wires one per-run graph (a Skill Build) end-to-end; the skill-building agents are the first producers, not the boundary of the concept.

The Shared Context Graph is state-managed as an append-only event ledger plus materialized current graph views. Old events are not mutated. Agents append context events; projections materialize current nodes/edges; UIs consume scoped views rather than rendering the full graph.

Canonical event fields include:

```yaml
event_id: sce_...
graph_id: skill_build:sbr_...
layer: long_term | working | reasoning
subject: skill:migration.lakebridge-reconcile
predicate: depends_on_skill
object_ref: skill:migration.lakebridge-support-matrix
value_json: {}
actor_type: agent | skill | tool | user | system
actor_id: reviewer_agent
trust_level: working | shared | published | verified
evidence_refs_json: []
sequence_no: 42
created_at_ms: 1760000000000
```

Storage decisions:

- **Lakebase is the live operational source of truth** for Shared Context Graph events, current nodes, current edges, Skill Requests, Skill Build Runs, task status, review state, and real-time UI reads.
- **UC Volumes hold large artifacts**, including draft `SKILL.yaml`, draft `skill.py`, test logs, review reports, evidence packs, migration inputs/outputs, and generated artifacts.
- **Approved BrickVision core skills live in `repo:/skills/<skill_name>/`**. This remains the source of truth for reviewed, CI-tested in-house skills.
- **Approved partner skills live in `repo:/skill-packs/<partner>/<skill_name>/`** or the signed packaged equivalent. UC Volumes may stage drafts but must not become the canonical approved skill source.
- **Vector Search indexes selected summaries**, such as skill descriptions, evidence-pack summaries, decision summaries, review findings, and artifact summaries. It must not embed every raw event.
- **UC Delta is optional archive/export/offline analytics**, not the live Shared Context Graph store.

**Skill body vs. skill state — the hard storage line.** Lakebase never stores skill source code. A skill's executable body and its lifecycle/graph state are different things and live in different places:

| Thing | Where it lives | Why |
|---|---|---|
| Approved skill body (`SKILL.yaml`, `skill.py`, tests) | Git: core in `repo:/skills/<name>/`, partner in `repo:/skill-packs/<partner>/<name>/` | Executable contracts must be code-reviewed, signed, and replayable from content hashes — never a mutable DB row. |
| Draft/in-progress skill artifacts (build outputs, logs, review reports, evidence packs) | UC Volumes | Large, transient, not yet trusted; staging area only. |
| Skill lifecycle + graph state (Skill Requests, Build Runs, task/review status, context events, node/edge projections, trust level, and **pointers** to the Git path or Volume path) | Lakebase | Live, queryable, real-time UI reads. |
| Curated summaries (skill descriptions for retrieval) | Vector Search | Semantic match during planning; not the source of truth. |

A skill becomes executable only when its file is reviewed/signed in Git, not when a Lakebase row flips. When an agent needs the actual body, it dereferences the Lakebase pointer to Git or the Volume. This keeps the trust boundary non-circular: Lakebase holds metadata and references, files hold code.

Visibility rule: the Console must not default to a raw node/edge hairball. It should expose context through lenses: memory layer summary, Skill Build vertical workflow, selected-node context panel, decision trace view, artifact view, review/test view, and event timeline.

---

## §24.0.B Context Intelligence Layer

The Shared Context Graph is the memory substrate. **Context Intelligence** is the attention/control layer over that memory. It decides what an agent actually sees, at what resolution, with what provenance, and under what budget.

Research translation:

- DeepSeek V4's useful lesson for BrickVision is not "send a huge prompt." It is long-context control: preserve high-resolution recent context, compress older context, and selectively retrieve older high-value blocks.
- BrickVision should apply the same product principle over enterprise memory: Hot Context for recent state, Warm Context for compressed long-range summaries, Cold Context for full artifacts retrieved only when needed.
- Context Intelligence is therefore the planner/retriever/compressor between Shared Context Graph memory and agent execution.

The product differentiator is:

> BrickVision does not just call agents. It gives agents the right enterprise context, at the right resolution, with provenance, trust, and memory.

Context tiers:

1. **Hot Context** — recent active run state, current user action, latest agent messages, current Skill Build tasks. This behaves like a local sliding window.
2. **Warm Context** — compressed summaries of relevant Skill Builds, evidence packs, decision traces, workspace facts, graph neighborhoods, and prior failures.
3. **Cold Context** — full UC Volume artifacts, archived events, older graph history, source docs, and raw logs. Retrieved only when needed.

Core flow:

```text
User / Agent Task
  -> Context Intent Classifier
  -> Selective Context Router
     -> Hot: live Lakebase state
     -> Warm: summaries + selected graph neighborhoods
     -> Cold: UC Volume artifacts / docs / raw evidence
  -> Prompt Context Pack
  -> Agent executes
  -> New events + summaries written back to Shared Context Graph
```

Responsibilities:

- Build task-specific context packs.
- Summarize old events into stable memory nodes.
- Rank graph neighborhoods for agent relevance.
- Track what context was shown to which agent.
- Store context-selection decisions as Reasoning Memory.
- Support "why did the agent know this?" in the UI.

Reasoning effort policy:

- Low-effort tasks use mostly Hot Context. Example: UI label cleanup or small contract explanation.
- Medium-effort tasks add Warm Context. Example: Skill Build task planning, migration workflow troubleshooting, or usecase planning.
- High-effort tasks may retrieve Cold Context. Example: Lakebridge reconcile skill build, partner skill import review, or production action planning.

Anti-pattern: do not use the Shared Context Graph as a raw prompt dump. The agent should not receive every Workspace Context claim, every skill file, every build event, or every artifact. It should receive a scoped Prompt Context Pack with provenance, trust level, recency, and reason for inclusion.

Implementation cut-line: Context Intelligence is the north-star layer. The first implementation should be a narrow **Context Pack Builder** for Skill Builds with Agents. It should read Lakebase state, pull only relevant skill/workspace/capability context, dereference only needed UC Volume artifacts, emit a prompt context pack per agent step, and append `context_pack_created` events for audit.

---

## §24.1 The Five Stages (Agents)

The Build-Pipeline Stages are the agents. Each stage has a distinct responsibility and a distinct relationship to the skill catalog:

| Stage | Role | Consumes | Produces | Skill Usage |
|-------|------|----------|----------|-------------|
| `stage:agent-design` | **The Architect** | Outcome spec + Databricks Capability Graph + Skill Knowledge Graph + Workspace Context Graph | Execution Plan (DAG of skills) | Selects published skills via Skill Knowledge Graph + `when_to_use` + `load_signal` matching |
| `stage:agent-generate` | **The Engineer** | Execution Plan + skill contracts | Executable artifacts (code, SQL, config) | Invokes LLM-backed skills (`model_role ≠ null`) |
| `stage:agent-validate` | **The Validator** | Generated artifacts + skill contracts | Validation results (pass/fail per scorer) | Runs `eval.scorers` against outputs |
| `stage:agent-evaluate` | **The Evaluator** | Validation results + outcome requirements | Go/no-go decision + recommendations | Applies `required_pass_rate` + outcome-level SLOs |
| `stage:agent-productionize` | **The Deployer** | Validated artifacts + approval gates | Deployed workspace resources | Invokes deployment skills with approval checkpoints |

These stages execute **sequentially** for a given outcome, with explicit handoff contracts between each stage.

---

## §24.2 Opportunity Discovery + Execution Lifecycle

Before a user writes an outcome spec, BrickVision runs an **Opportunity Discovery** pass over the active Workspace Context. This pass is read-only and does not create resources. Its job is to answer: "Given what exists here, what can BrickVision usefully build next?"

Inputs:

- Workspace Context from `workspace_claims_current_synced`: catalogs, schemas, tables, views, volumes, functions, metadata, owners, and refresh run ids.
- Capability Graph: extensions, source provenance, hand-authored exemplar links, and multi-hop related capabilities.
- Workspace profile scope: allowed catalogs, blocked catalogs, read-only/write-capable policy, cloud/region, and partner skill packs.

Outputs:

```yaml
suggestion_id: suggestion:customer-360-feature-pipeline
title: Build a customer 360 feature pipeline
why_this_workspace: |
  The workspace contains customer, order, spend, and tiering objects under
  partner_demo_catalog.mfg_agent_bricks_demo plus customer-related functions.
workspace_evidence:
  - table:partner_demo_catalog.mfg_agent_bricks_demo.customers
  - table:partner_demo_catalog.mfg_agent_bricks_demo.orders
  - function:partner_demo_catalog.mfg_agent_bricks_demo.fn_customer_tier
capability_evidence:
  - meta:data-engineering/ext:dlt-pipeline
  - meta:delta-lake/ext:merge
  - meta:governance/ext:table-quality-monitoring
suggested_outcome_spec:
  objective: Build a governed customer feature table refreshed daily.
  data_sources:
    - existing_uc_table: partner_demo_catalog.mfg_agent_bricks_demo.customers
    - existing_uc_table: partner_demo_catalog.mfg_agent_bricks_demo.orders
  expected_artifacts:
    - lakeflow_pipeline
    - feature_table
    - data_quality_checks
missing_inputs_or_questions:
  - Which schema should own generated artifacts?
  - Is this a prototype or production deployment?
confidence: 0.78
estimated_complexity: medium
next_action: plan_and_build
```

Suggestion categories at v0.7.9:

- **Data product build**: pipelines, curated tables, feature tables, semantic models.
- **Quality and governance build**: expectations, monitors, tags, masking, lineage summaries.
- **Agent/tool build**: UC functions, Databricks Apps, model serving endpoints, retrieval tools.
- **Documentation and migration build**: workspace maps, lineage inventories, migration assessments.
- **Optimization build**: cost/freshness/ownership cleanup tasks when enough metadata exists.

An accepted suggestion becomes an Outcome Specification and enters the five-stage lifecycle below. The user can edit the suggestion before planning; the edited form becomes the replay-pinned input.

### §24.2.A UI separation of concerns

The Console must expose Opportunity Discovery without making every concept appear on one page. The product has five separate surfaces:

| Surface | Primary question | Shows | Must not show |
|---|---|---|---|
| **Workspace Context** | What does BrickVision know about this workspace? | Evidence summaries, claims, schema/table profile readiness, and **Evidence Starters**. | Full business-usecase lifecycle, deployment controls, or Skill Library editing. |
| **Usecase Candidates** | What business outcomes look possible here? | Candidate outcomes, persona, value hypothesis, data readiness, required skill families, missing inputs, and confidence. | Raw SQL as the main result or "complete" lifecycle badges. |
| **Usecase Detail** | How will one chosen outcome be built? | Outcome, evidence, strategy, skills, artifacts, validate, evaluate, deploy, and outcome proof as tabs or a left-side stepper. Only one detailed section is visible at a time. | Unrelated workspace claim lists or unrelated candidate cards. |
| **Skill Library** | What skills can agents use, request, build, review, or import? | Available Skills, Skill Requests, Skill Builds, Draft Skills, Partner Skills, capability anchors, tool/runtime requirements, eval scorers, signing, and validation status. | Business outcome execution status unless opened from a skill request tied to an execution. |
| **Evaluation** | Is BrickVision performing well enough to trust and promote? | MLflow evaluation dataset coverage, latest scorer runs, pass rates, quality gates, runtime events, record previews, and MLflow run/trace IDs when available. Trend charts and failing-record drilldowns are planned follow-ons. | Raw platform telemetry, generic infra uptime, or token/cost-only charts that belong in Observability. |

The Workspace Context page is evidence-first. It may surface **Evidence Starters** such as "schema profile quality starter" or "migration source artifact detected," but those are not business usecases. The primary action from Workspace Context is to inspect evidence or open/create a candidate usecase. If the only buildable artifact is a SQL view, the UI must label it as a **technical starter artifact**.

The Usecase Candidates page is business-first. A card is only a business usecase candidate when it includes:

1. a business outcome or operational objective;
2. an intended persona or owner;
3. a value hypothesis or measurable outcome;
4. workspace evidence explaining why the candidate is relevant;
5. required skill families, at minimum SQL, PySpark, ML, AI, migration, and deployment where applicable;
6. missing skills or missing evidence as explicit gaps.

The Usecase Detail page is execution-first. It should use tabs or a side-stepper:

1. **Outcome** — objective, persona, value hypothesis, acceptance criteria.
2. **Evidence** — workspace claims, source tables, files, functions, jobs, model endpoints, and evidence quality.
3. **Strategy** — SQL-only, PySpark pipeline, ML workflow, AI agent, migration assessment, or composite build strategy.
4. **Skills** — selected skills, available/requested/draft status, partner-provided skills, Skill Library entry points.
5. **Artifacts** — generated SQL views, PySpark notebooks/jobs, ML experiments/models, AI-agent configs, dashboards, alerts.
6. **Validate** — correctness checks, data-quality checks, model/agent evals, migration reconcile checks.
7. **Evaluate** — business acceptance, cost/value tradeoff, risk, go/no-go decision.
8. **Deploy** — jobs/endpoints/apps/dashboards/permissions with approval gates.
9. **Outcome Proof** — measured result after deployment.

This separation is mandatory because SQL, PySpark, ML, AI, and migration artifacts are implementation details. A usecase can contain many artifacts across multiple skill families; no artifact family is allowed to define the product surface by itself.

The Evaluation page is mandatory because "evaluate" is not the same as "observe." Observability answers whether BrickVision is running and what it costs. Evaluation answers whether BrickVision's outputs are correct, grounded, useful, reproducible, and promotion-ready. Evaluation is therefore a top-level route (`/evaluation`) backed today by `/api/knowledge/evaluation/overview` and `/api/knowledge/evaluation/datasets/{id}/records`, not a panel under `/observability`.

---

## §24.2.1 Execution Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        OUTCOME SPECIFICATION                              │
│  (accepted suggestion OR natural language OR structured YAML — see §24.3) │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: DESIGN (stage:agent-design)                                    │
│                                                                          │
│  1. Parse accepted suggestion/outcome into intent + constraints + data   │
│     sources                                                              │
│  2. Retrieve relevant extensions from Capability Graph (VS semantic      │
│     search + multi-hop PPR expansion)                                    │
│  3. Resolve Workspace Context evidence (what already exists — UC         │
│     objects, functions, volumes, compute, existing pipelines)            │
│  4. Match skills via `when_to_use` + `load_signal.triggers`             │
│  5. Resolve skill dependencies (`requires.skills` field)                 │
│  6. Build execution DAG (topologically sorted, parallelizable where      │
│     skills have no data dependency)                                      │
│  7. Estimate budget (sum of selected skills' `requires.budget`)          │
│  8. Emit: ExecutionPlan                                                  │
│                                                                          │
│  OUTPUT: ExecutionPlan {                                                 │
│    outcome_id, stages: [SkillInvocation(skill_id, bound_inputs,          │
│    expected_outputs, budget_allocation)], total_budget, approval_gates    │
│  }                                                                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 2: GENERATE (stage:agent-generate)                                │
│                                                                          │
│  For each SkillInvocation in plan (topological order):                   │
│    1. Resolve inputs (from prior outputs, user-provided, or KG)          │
│    2. Check `requires` (permissions, tools, budget remaining)            │
│    3. If `model_role != null`: call worker agent with constitutional       │
│       constraints through the tool-based harness (§24.7.A)                │
│       - System prompt includes: skill description + grounding evidence   │
│         from Capability Graph + input schemas + output schemas           │
│       - Constitutional rules enforced as hard filters on output          │
│    4. If `model_role == null` (mechanical): execute tool calls directly  │
│    5. Verifier agent checks worker/tool output before promotion           │
│    6. Validate output schema matches `outputs` declaration              │
│    7. On failure: apply `execution.on_failure` policy                    │
│    8. Emit: SkillExecutionResult (typed audit row)                       │
│                                                                          │
│  OUTPUT: GenerationResult {                                              │
│    artifacts: [SkillExecutionResult], budget_consumed, failures          │
│  }                                                                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: VALIDATE (stage:agent-validate)                                │
│                                                                          │
│  For each SkillExecutionResult:                                          │
│    1. Run declared `eval.scorers` against outputs                        │
│    2. Compare against `eval.required_pass_rate`                          │
│    3. Flag constitutional violations (if any slipped through)            │
│    4. Emit: ValidationResult (per-skill pass/fail + scores)              │
│                                                                          │
│  OUTPUT: ValidationReport {                                              │
│    per_skill_results: [{skill_id, pass, scores, violations}],            │
│    overall_pass_rate, blocking_failures                                   │
│  }                                                                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: EVALUATE (stage:agent-evaluate)                                │
│                                                                          │
│  1. Check ValidationReport.overall_pass_rate against outcome SLOs        │
│  2. If below threshold: recommend retry with alternative skills OR       │
│     surface as Question for human review                                 │
│  3. If passes: emit go/no-go recommendation                             │
│  4. Check approval gates — if any gate requires human sign-off,          │
│     pause execution and emit ApprovalRequest                             │
│                                                                          │
│  OUTPUT: EvaluationDecision {                                            │
│    decision: "proceed" | "retry" | "abort" | "await_approval",           │
│    approval_requests: [ApprovalRequest], recommendations                 │
│  }                                                                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼ (only if decision == "proceed")
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 5: PRODUCTIONIZE (stage:agent-productionize)                      │
│                                                                          │
│  For each deployment-class skill in the plan:                            │
│    1. Execute deployment actions (create Jobs, endpoints, schedules)      │
│    2. Verify deployed resources are healthy                              │
│    3. Configure monitoring/alerting as specified in outcome               │
│    4. Emit: DeploymentResult (resource URIs, health status)              │
│                                                                          │
│  OUTPUT: OutcomeResult {                                                 │
│    outcome_id, status: "deployed" | "partial" | "failed",               │
│    deployed_resources: [ResourceURI], audit_trail_id                     │
│  }                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## §24.3 Outcome Specification

An outcome can be specified in three forms:

**Form A — Accepted build suggestion** (produced by Opportunity Discovery, optionally edited by the user):
```yaml
suggestion_id: suggestion:customer-360-feature-pipeline
accepted_by: user:suneel.sunkara@databricks.com
objective: Build a governed customer 360 feature pipeline from existing workspace assets.
workspace_evidence:
  - table:partner_demo_catalog.mfg_agent_bricks_demo.customers
  - table:partner_demo_catalog.mfg_agent_bricks_demo.orders
capability_evidence:
  - meta:data-engineering/ext:dlt-pipeline
  - meta:delta-lake/ext:merge
expected_artifacts:
  - lakeflow_pipeline
  - feature_table
  - data_quality_checks
approval_gates:
  - before: deploy_to_production
    approver: workspace-owner
```

**Form B — Natural language** (parsed by `stage:agent-design`):
```
"Build a DLT pipeline that ingests customer events from S3, validates schema,
 computes RFM features, and deploys a churn prediction model to a serving endpoint"
```

**Form C — Structured YAML** (directly consumed by `stage:agent-design`):
```yaml
outcome: customer-churn-prediction
version: 1
objective: |
  Predict which customers will churn in the next 30 days
  using historical transaction and engagement data.

data_sources:
  - name: transactions
    location: s3://partner-data/transactions/
    format: parquet
    freshness: daily

requirements:
  quality:
    - "no null values in customer_id"
    - "transaction_amount must be positive"
  performance:
    - "model AUC > 0.82"
    - "inference latency < 100ms p99"
  governance:
    - "PII columns tagged and masked for non-admin roles"

approval_gates:
  - before: deploy_to_production
    approver: ml-platform-team
```

---

## §24.4 Configuration Bundle Model

BrickVision has two different configuration concerns and they must not be collapsed:

1. **BrickVision control-plane configuration** — where BrickVision itself runs, where its catalog/schema/VS index/Lakebase live, and which model endpoints it uses.
2. **Target workspace configuration** — which partner or customer workspace BrickVision is allowed to inspect or operate against, what scopes are allowed, and which credentials should be used.

The target workspace configuration is **not** stored in UC. UC is a data plane dependency that is available only after authentication succeeds. Storing workspace connection profiles there would make the bootstrap trust boundary circular and would make multi-workspace operation harder to reason about.

### §24.4.1 Directory layout

The canonical local/deployable layout is:

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
      delta.sql-transform/SKILL.yaml
  partner-acme/
    pack.yaml
    skills/
      telco.churn-feature-builder/SKILL.yaml
      telco.network-quality-kpi/SKILL.yaml
```

`.env` is limited to bootstrap pointers:

```ini
BV_CONFIG_DIR=config
BV_ACTIVE_WORKSPACE_PROFILE=partner-dev
BV_SKILL_PACK_MANIFEST=config/skill-packs.yaml
```

### §24.4.2 Workspace profile YAML

Workspace profiles describe **where BrickVision is allowed to look and act**. They contain no raw secrets, only secret references.

```yaml
schema_version: 1.0
kind: workspace_profile

id: partner-dev
partner_id: acme-si
display_name: Acme SI Dev Workspace

workspace:
  host: https://adb-xxx.azuredatabricks.net
  workspace_id: "123456789"
  cloud: azure
  region: eastus

auth:
  mode: service_principal_oauth
  client_id_secret_ref: databricks-secret-scope/bv-partner-client-id
  client_secret_secret_ref: databricks-secret-scope/bv-partner-client-secret

scope:
  read_only: true
  allowed_catalogs:
    - main
    - partner_dev
  blocked_catalogs:
    - system

kg:
  enabled: true
  introspection:
    include_catalogs: true
    include_lineage: true
    include_jobs: false
    include_models: false
```

### §24.4.3 Config hash and audit rule

Every run records:

- `workspace_profile_id`
- SHA-256 hash of the normalized workspace profile YAML
- SHA-256 hash of `skill-packs.yaml`
- hashes of every loaded `pack.yaml` and `SKILL.yaml`
- loader version
- active principal / actor identity

The YAML files are the **declared intent**. UC Delta tables store the **observed results**: workspace claims, freshness beliefs, questions, audit rows, and outcome execution state. This separation makes replay possible without turning UC into the bootstrap control plane.

---

## §24.5 Partner Skill Pack Model

Current BrickVision core skills live under the repo's `skills/` directory. The authoritative contract is each `SKILL.yaml`; `skill.py` files are not the source of truth for planning until the Skill Executor exists.

Partner-authored skills should not be added by editing BrickVision's core `skills/` tree. They are delivered as **skill packs**:

```yaml
# skill-packs/partner-acme/pack.yaml
schema_version: 1.0
kind: skill_pack

id: partner-acme
partner_id: acme-si
title: Acme Telco Delivery Accelerators
version: 0.1.0
owner: acme-si/platform-engineering
trust:
  mode: partner_signed
  signing_key_id: acme-si-prod-2026

skills:
  - path: skills/telco.churn-feature-builder/SKILL.yaml
  - path: skills/telco.network-quality-kpi/SKILL.yaml
```

And enabled from the deployment-level manifest:

```yaml
# config/skill-packs.yaml
schema_version: 1.0
kind: skill_pack_manifest

enabled_packs:
  - id: brickvision-core
    path: skill-packs/brickvision-core
    trust: brickvision_signed
  - id: partner-acme
    path: skill-packs/partner-acme
    trust: partner_signed
    partner_id: acme-si
```

Each partner skill must still declare:

- `id` under a partner-owned namespace, e.g. `skill:partner.acme.telco.churn-feature-builder`
- `exemplar_of: meta:<m>/ext:<e>` for the primary capability anchor
- `capability_links` when the skill is composite and spans multiple Capability Graph extensions
- owner and signing key
- typed inputs and outputs
- `requires.tools`
- `when_to_use`
- `eval.scorers`
- constitutional constraints

Partner skills become visible to agents only after validation. Validation checks schema, namespace ownership, signature/trust policy, `exemplar_of` pointer shape, `capability_links`, required tools, and whether every target extension exists in the active Capability Graph snapshot or is explicitly marked `capability_anchor_status: pending_indexer_stub`.

### §24.5.1 Skill Library is the skill lifecycle path

Skill Library is the product surface for viewing published skills and managing the custom skill lifecycle. It does not replace `SKILL.yaml`; it produces, imports, validates, and publishes signed skill artifacts. The canonical artifact remains the signed file bundle, but BrickVision teams and partners should not have to start from a blank YAML file.

Skill location policy:

- `repo:/skills/<skill_name>/` is the canonical location for approved BrickVision core skills.
- `repo:/skill-packs/<partner>/<skill_name>/` is the canonical location for approved partner-authored skills after trust review, validation, signing, and pack enablement.
- UC Volumes are the staging and evidence location for active Skill Builds: draft source files, logs, test outputs, review reports, and generated artifact bundles.
- Lakebase stores lifecycle metadata and graph state: Skill Requests, Skill Build Runs, draft/published status, event history, node/edge projections, trust levels, and references to repo paths or UC Volume artifact paths.
- Vector Search stores semantic summaries for retrieval. It is not the source of truth for skill source code or lifecycle state.

Skill Library lifecycle:

1. A Skill Request is created from a user need, execution blocker, Capability Graph area, or partner playbook.
2. A Skill Build Run coordinates planner, evidence, runtime, test, and reviewer agents.
3. Agents propose draft artifacts: `SKILL.yaml`, `skill.py`, tests, readiness checks, and UI metadata.
4. BrickVision validates the draft locally and in CI: schema, namespace, capability links, permissions, side effects, eval definitions, signing policy, replay hash, and active-snapshot anchor resolution.
5. The validated skill is published to the Skill Library, optionally packaged into `skill-packs/<partner>/...`, signed, and enabled through `config/skill-packs.yaml`.
6. Build Suggestions and `stage:agent-design` may now select the published skill when Workspace Context, Skill Knowledge Graph, and Capability Graph evidence match its `when_to_use` and `load_signal`.

Skill Library has five sections:

- **Available Skills**: published in-house and partner skills agents can use now.
- **Skill Requests**: requested custom in-house or partner skills to build, import, reject, or defer.
- **Skill Builds**: active and historical agent-assisted build runs.
- **Draft Skills**: generated artifacts awaiting tests, review, or publication.
- **Partner Skills**: externally supplied skills entering trust, test, and import review.

Skill Library must not publish ungrounded skills. Every generated or imported skill requires at least one Capability Graph anchor or an explicit pending-stub marker, and every write-side tool must have an approval or prototype-scope policy.

### §24.5.2 Starter templates are prescriptive, not restrictive

Starter templates are BrickVision-authored examples that show users how to build with the current skills. They are not the limit of what Skill Library can request/build/import, and they are not automatically business usecases.

There are two template levels:

1. **Evidence starter**: proves that BrickVision can build or analyze one artifact from the current workspace evidence. Examples: SQL profile view, PySpark data-quality notebook, ML feature-readiness scan, AI retrieval corpus inventory, Lakebridge SQL transpilation proof.
2. **Business usecase template**: composes evidence starters and skills into an outcome with a persona, value hypothesis, acceptance criteria, validation/evaluation plan, deployment path, and outcome proof. Examples: reduce overspend leakage, accelerate Teradata migration assessment, improve customer churn intervention, govern PII access, build a support-agent knowledge base.

An evidence starter may become one artifact in a business usecase, but the UI and persistence model must keep that distinction visible.

Template selection is evidence-led. A template can be shown as "ready" only when the active Workspace Context and Capability Graph contain the required evidence. Otherwise it is hidden or shown as "needs more context" with the exact missing input or refresh task.

The v0.7.9 live-data pass observed:

- Workspace Context has 208 current claims: 171 tables, 23 function-like concepts, 13 schemas, and 1 catalog.
- All current Workspace Context claims are from `skill:uc.catalog-introspect` and only assert `EXISTS` / `BELONGS_TO`.
- No current Workspace Context claims describe lineage, grants, table profiles, quality metrics, row counts, model endpoints, jobs, or migration source systems.
- The live Capability Graph has 467 extensions, with strong coverage in data engineering, governance, ML, GenAI, and migration, but zero live exemplar links in `extensions_synced`.
- Workspace name signals are strongest for customer/order/product data products, banking/spend optimization, manufacturing demo objects, knowledge-base/vector-search assets, and BrickVision's own internal tables. Migration source-system signals are absent in the current workspace.

The first proof build corrected the template list:

- `partner_demo_catalog.datagraph_schema` looked like a good customer/order template from names and columns, but all five candidate tables had zero rows. It is not a ready starter template.
- `partner_demo_catalog.spend_optimization` passed the data gate: 200 customers, 2,400 enriched spend rows, 5 spend categories, no null customer/month/spend fields, and meaningful overspend signals.
- BrickVision built `partner_demo_catalog.brickvision.bv_proof_spend_optimization_insights` from `spend_optimization.silver_spend_enriched`.
- Validation passed: one output row per source customer (`200` source customers → `200` output rows), no null customer ids, no null recommended actions, and no null highest-overspend categories.
- The output classified 39 customers as `high_priority_budget_review`, 50 as `targeted_category_review`, 80 as `monitor_budget_utilization`, and 31 as `no_action_required`.

This means the first evidence starters should be conservative:

| Evidence starter | Ready from current data? | Why |
|---|---:|---|
| Workspace inventory and context map | Yes | Requires only UC object existence claims. |
| Spend optimization insight table | Proven | Built and validated against real `spend_optimization` data; safe first `delta.sql-transform` starter. |
| Customer/order data-product transform | Not ready from names alone | `datagraph_schema` has the right table shapes but zero rows; only show if row-count/table-profile evidence is available. |
| Table profiling and layout recommendation | Needs on-demand table introspection | Core skills exist, but current Workspace Context lacks table size/history/profile claims. |
| Governance taxonomy and grant plan | Needs grants refresh or user-supplied policy | Core design skills exist, but current Workspace Context lacks grant/tag claims. |
| Knowledge-base / RAG asset builder | Candidate only | Workspace has knowledge-base/vector-search-looking assets, but no first-class RAG builder skill exists yet. This should become a Skill Library request/template or partner skill. |
| ML train/register/serve | Proven through strategy/backend gates | Live data supports `Recurring Expense Cancellation Classifier`: `recurring_expenses.cancellable_flag` is the target label, `customer_id` is the entity key, and `customer_profile` + `enriched_transactions` provide supporting features. Training remains approval/artifact-gated. |
| Lakebridge SQL transpilation starter | Proven with remediation | Lakebridge parsed/analyzed/transpiled a Teradata-style SQL artifact, BrickVision caught a Databricks validation failure, then `delta.sql-transform` style remediation produced a runnable Databricks artifact. Full Lakebridge assessment/reconcile remains gated. |

The first business usecase templates should be introduced after evidence starters are shown honestly:

| Business usecase template | Status | Why |
|---|---:|---|
| Spend optimization intervention | Candidate | Evidence exists for an insight artifact, but the usecase still needs persona, business acceptance criteria, remediation workflow, and outcome measurement. |
| Migration assessment and remediation | Candidate | Lakebridge SQL transpilation is proven as an artifact, but full assessment/reconcile requires source-system artifacts, runtime tools, and partner migration skills. |
| Customer 360 / churn intervention | Candidate only | Current workspace has some name signals but insufficient row-count/profile/label evidence. |
| Knowledge-base / AI support agent | Candidate only | Workspace has vector-search-like assets, but a validated AI/RAG builder skill and evaluation plan are not yet available. |
| Recurring expense cancellation classifier | Proven through ML planning gates | Current workspace has a concrete label (`cancellable_flag`), feature sources, and a supported `classification` strategy. The UI must stop at the required strategy approval and training-artifact URI gates until a real Databricks artifact is bound. |

The product should ship proven evidence starters first, show candidate business usecases only with missing-evidence explanations, and teach users how to request, build, import, review, and publish new skills through Skill Library. A "ready" business usecase requires both evidence readiness and skill-composition readiness across the required artifact families: SQL, PySpark, ML, AI, migration, governance, and deployment where applicable.

### §24.5.2.A May 15, 2026 Usecase Detail validation status

The current local console validated the core Usecase Detail build path for `uc_41a206508d77cc14bd78054e`, but not every button on the page. Product language must distinguish **proven**, **expected-gated**, and **not yet validated** surfaces.

Validated:

- `Save Required Details`
- `Save Build Path`
- `Save SQL/PySpark/ML/Deploy Details`
- `Prepare Build Plan`
- `Run SQL Build` → `execution_proven`
- `Run PySpark Build` → `execution_proven`
- `Run ML Build` → `classification` path reaches `ready_for_strategy`, `feature_ready`, `ready_for_approval`, `model_family=ready`, and `backend_selection=ready` with `selected_backend=databricks_mlflow_flavor_job`
- `Run Quality Check` → `passed`
- `Check Review Readiness` → `ready_for_execution`

Expected-gated:

- Full ML training remains blocked until `strategy_approval_id` and `training_artifact_uri` are bound. This is the correct gate and must not be bypassed with custom BrickVision training code.
- AI remains blocked until a first-class AI/RAG execution skill exists.
- Deploy readiness is limited to input binding and planning; no deploy execution proof has been validated.

Not yet validated:

- Execution Monitor start buttons and live timeline.
- Successful AI tool execution.
- Full Databricks ML training/register execution after a strategy approval and real training artifact are bound.

The candidate generator must not surface unsupported ML strategies as `ML: recommended`. The previous "Personalized Spend Recommendation Model" routed to `ranking`, lacked an observed response label, and was rejected by all registered ML backends. The current ML candidate is `Recurring Expense Cancellation Classifier`, backed by `recurring_expenses`, `customer_profile`, and `enriched_transactions`.

### §24.5.2.B Tested template promotion rule

A starter template moves from "candidate" to "ready" only after a proof run records:

1. **Workspace evidence**: exact source tables/functions/volumes from Workspace Context.
2. **Data evidence**: non-zero row counts or other domain-specific availability checks.
3. **Capability evidence**: matching Capability Graph extensions or explicit pending-stub markers.
4. **Build artifact**: an actual table, view, notebook, SQL file, app, or skill pack produced in the target workspace or local workspace.
5. **Validation result**: at minimum row-count, nullability, key uniqueness, and expected-action distribution checks for data-product templates.
6. **PRD update**: only the proven behavior is documented as ready; rejected candidates are documented with the reason they failed.

This rule prevents BrickVision from suggesting a template because object names look plausible while the underlying data cannot support the build.

### §24.5.2.B Tested Lakebridge starter template

BrickVision should ship a Lakebridge starter template because migration is a high-value partner accelerator and the live Capability Graph already contains concrete migration anchors. The tested starter is **Lakebridge SQL transpilation with BrickVision remediation**, not the full assessment/reconcile suite.

Proof run:

- Installed `databricks-labs-lakebridge==0.12.2` in isolated `.lakebridge-venv` so the main BrickVision app environment was not downgraded.
- Created `proof-artifacts/lakebridge/source/teradata_spend_customer_rollup.sql`, a Teradata-style SQL artifact for a spend optimization rollup.
- Ran Lakebridge `SqlglotEngine` against the artifact.
- Lakebridge parse succeeded with one parsed expression and lineage extraction found `bronze_customers -> v_spend_customer_rollup` and `bronze_transactions -> v_spend_customer_rollup`.
- Lakebridge transpilation to Databricks returned `success_count=1`, but Databricks `EXPLAIN` failed because Teradata `FORMAT` syntax remained in the generated SQL.
- BrickVision applied a `delta.sql-transform` style remediation: replaced unsupported `FORMAT` / `NULLIFZERO` syntax, mapped legacy table names to `partner_demo_catalog.spend_optimization`, and materialized `partner_demo_catalog.brickvision.bv_proof_lakebridge_spend_rollup`.
- Validation passed: source customers `200`, output rows `100` after the source SQL's `TOP 100` semantics, no null customer ids, no null budget statuses, and bounded one-row-per-customer behavior.

This proves a multi-skill starter flow:

1. `skill:uc.catalog-introspect` supplies Workspace Context evidence that candidate source tables exist.
2. Partner Lakebridge skill parses, extracts lineage, and transpiles source SQL.
3. `skill:delta.sql-transform` validates/remediates generated SQL and builds the Databricks artifact.
4. Validation records whether raw transpilation was sufficient or required remediation.

Live capability anchors observed in v0.7.9:

- `meta:migration-assessment/ext:execute-database-profiler`
- `meta:migration-assessment/ext:configure-reconcile`
- `meta:migration-analysis/ext:analyze`
- `meta:migration-analysis/ext:parse-sql`
- `meta:migration-transpile/ext:transpile`
- `meta:migration-transpile/ext:transpile-sql`
- `meta:migration-transpile/ext:generate-sql`
- `meta:migration-validation/ext:reconcile`
- `meta:migration-validation/ext:reconcile-data`
- `meta:migration-validation/ext:check-table-mismatch`

Tested Skill Library draft skeleton:

```yaml
id: skill:partner.<partner_id>.migration.lakebridge-sql-transpile
title: Lakebridge SQL transpilation starter
version: "0.1.0"
exemplar_of: meta:migration-transpile/ext:transpile-sql
capability_anchor_status: active_snapshot_verified
capability_links:
  primary:
    - meta:migration-transpile/ext:transpile-sql
  uses:
    - meta:migration-analysis/ext:analyze
    - meta:migration-analysis/ext:parse-sql
    - meta:migration-transpile/ext:transpile
    - meta:migration-transpile/ext:transpile-sql
    - meta:migration-transpile/ext:generate-sql
  validates:
    - meta:migration-validation/ext:validate-input
when_to_use:
  - Source-system SQL files are available in a UC Volume, workspace file path, or Git path
  - Partner asks for SQL migration, source SQL analysis, transpilation, or Databricks SQL remediation
requires:
  tools:
    - tool:lakebridge.analyze_sql
    - tool:lakebridge.transpile_sql
    - tool:data_pipeline.submit_sql
    - tool:kg.emit_claims
  skills:
    - skill:uc.catalog-introspect@>=0.1
    - skill:delta.sql-transform@>=0.1
inputs:
  - name: source_system
    type: string
    required: true
    enum: [teradata, snowflake, oracle, redshift, synapse, sqlserver, netezza, unknown]
  - name: source_artifact_location
    type: string
    required: true
    description: UC Volume path, workspace file path, or Git path containing source SQL
  - name: target_catalog
    type: string
    required: true
outputs:
  - name: transpilation_report
    type: object
  - name: remediated_databricks_sql
    type: string
  - name: build_validation
    type: object
eval:
  scorers:
    - MigrationArtifactPresence
    - TranspilationCoverage
    - DatabricksSqlValidation
    - RemediationRequiredDisclosure
constitutional:
  - source.credentials.must.use.secret_refs
  - no.production.write.without.approval
  - generated.sql.must.be.grounded.in.source_artifacts
```

Readiness gates:

1. **Artifact gate**: at least one source SQL artifact exists and is readable.
2. **Source-system gate**: `source_system` is known or explicitly set to `unknown` with a Question for user confirmation.
3. **Target gate**: target catalog/schema is inside the active workspace profile scope.
4. **Tool gate**: Lakebridge runtime tools are available in the execution environment, or the template remains a Skill Library draft.
5. **Validation gate**: raw transpiled SQL must pass Databricks validation or produce a remediation step with explicit unsupported syntax findings.
6. **Build gate**: remediated SQL produces an artifact and validation checks pass.

Full Lakebridge assessment/profiling/reconciliation remains a separate candidate template. It is not promoted to ready until `tool:lakebridge.profile_source`, Spark/Databricks Connect reconcile runtime, and source/target comparison artifacts are tested end-to-end.

### §24.5.2.C Tested Lakebridge Code Convert starter

The original migration design treated "migration" too broadly and risked blending SQL transpilation, PySpark conversion, assessment, and reconciliation into one surface. The Databricks-backed Lakebridge lifecycle should be explicit:

1. **Assessment / Analyzer**: inspect source systems, source artifacts, lineage, dependencies, complexity, and migration blockers.
2. **Converter / Transpiler**: convert source SQL or source code into Databricks-targeted artifacts.
3. **Validate / Reconcile**: validate generated SQL/code, or reconcile migrated data, depending on artifact type.

The tested direction is split into product families:

1. **Migration Assessment**: candidate surface for Lakebridge analysis and estate profiling.
2. **SQL Transpile**: Lakebridge SQL transpilation plus BrickVision SQL validation/remediation.
3. **Code Convert**: Lakebridge Switch conversion for legacy PySpark code.
4. **Data Reconcile**: candidate surface for source-target reconciliation after migration.

Code Convert is intentionally separate from SQL Transpile. It uses the Lakebridge Switch CLI/job path, not custom BrickVision conversion logic. BrickVision's responsibility is orchestration, preflight, artifact routing, and honest status display.

Tested Code Convert flow:

- Source legacy PySpark is stored in a UC Volume under `LLM`-independent governed storage, for example `/Volumes/<catalog>/<schema>/<volume>/lakebridge/pyspark/source`.
- Converted output is expected to land in a UC Volume output path after Switch produces Workspace artifacts.
- Switch requires an internal `/Workspace/Users/<workspace-user>/...` output folder. BrickVision must create that folder before submitting the Switch job, then export/copy artifacts back to the UC Volume.
- Lakebridge Switch submits an asynchronous Databricks job. The UI must not present this as a fast local proof; it should show the Databricks run URL, remote state, and artifact/export status.
- The model endpoint for Switch is `LLM_GENERAL_TASKS`; embeddings use only `LLM_EMBEDDING_TASKS`.

Readiness gates:

1. **Source path gate**: source path must be a UC Volume directory containing legacy PySpark files.
2. **Output path gate**: converted output path must be a UC Volume directory.
3. **Workspace output gate**: internal Workspace output folder must exist before Switch submission.
4. **Switch install gate**: Lakebridge and Switch job must be installed and visible to the Databricks CLI.
5. **Remote run gate**: the Switch Databricks job must reach terminal success before BrickVision exports artifacts.
6. **Artifact gate**: converted files or Switch result-table diagnostics must be visible in the UI. A result table with generated content but failed export is a partial success, not a complete conversion.

### §24.5.3 Composite skill links

The original `exemplar_of: meta:<m>/ext:<e>` field works for single-capability skills, but partner accelerators often compose multiple capabilities. A migration accelerator is one example, but the same pattern applies to customer 360, governance rollout, RAG app build, ML deployment, and FinOps optimization.

Composite skills keep `exemplar_of` as the primary anchor for ranking and display, and add `capability_links` for the full capability footprint:

```yaml
id: skill:partner.acme.migration.teradata-to-databricks-assessment
version: "0.1.0"
exemplar_of: meta:migration-assessment/ext:assess-source-estate
capability_anchor_status: pending_indexer_stub
capability_links:
  primary:
    - meta:migration-assessment/ext:assess-source-estate
  uses:
    - meta:migration-analysis/ext:parse-sql
    - meta:migration-transpile/ext:transpile
    - meta:migration-validation/ext:reconcile
  validates:
    - meta:migration-validation/ext:check-table-mismatch
```

`capability_links` are used by:

- **Build Suggestions**: rank validated partner skills above generic generation when Workspace Context matches their evidence requirements.
- **Planner**: include the full capability footprint when building the execution DAG, not only the primary anchor.
- **Validation**: require evidence and tests for every linked capability that the skill claims to use or validate.
- **Provenance**: show the partner's methodology as a graph of capabilities, tools, and expected artifacts.

### §24.5.4 Lakebridge is an example, not the boundary

Lakebridge is a good example because the live Capability Graph has migration extensions and the migration domain is naturally composite: assessment, analysis, transpilation, reconciliation, lineage, and upgrades. But the PRD must not make Lakebridge the only partner-skill path.

The generic rule is:

- Capability Graph provides the capability anchors.
- Workspace Context provides applicability evidence.
- Starter templates show safe, pre-created examples that build with current skills.
- Skill Library teaches partners how to request/import/build new skills when no starter template fits.
- Partner skill packs package the validated result.
- Build Suggestions select the skill only when the active workspace evidence supports it.

### §24.5.5 Why skill packs are not UC rows

Skill packs are executable contracts and must be versioned like source artifacts. They belong in Git, a deploy bundle, Databricks Workspace Files, or a UC Volume as immutable files. UC tables may record which packs were loaded for a run, but the table is not the source of truth for the skill body.

This preserves:

- code review and signing before a skill becomes executable
- deterministic replay from content hashes
- clean separation between core PS/FDE skills and partner IP
- multi-partner operation without modifying BrickVision's repo

---

## §24.6 Skill Resolution Protocol

How Opportunity Discovery and `stage:agent-design` select which skills to invoke:

### §24.6.1 Resolution algorithm

```
0. If no explicit outcome exists:
   a. Read Workspace Context claims for the active workspace profile
   b. Group assets into candidate build contexts: data products, governance
      gaps, agent/tool opportunities, migration/lineage maps, optimization
      targets
   c. Retrieve related Capability Graph extensions for each candidate
   d. Rank suggestions by workspace evidence strength, capability coverage,
      exemplar availability, missing-input count, and estimated risk
   e. Emit BuildSuggestion records with suggested_outcome_spec payloads
1. When the user accepts or edits a suggestion, OR submits an explicit
   outcome, PARSE it into intent signals (verbs + nouns + constraints)
2. EMBED intent signals using the same embedding model as the Capability Graph
3. RETRIEVE top-K extensions from VS index (semantic match)
4. EXPAND via multi-hop PPR walk (4-hop, alpha=0.85) to find related extensions
5. For each retrieved extension:
   a. Check if it has an `exemplar_skill_id` (hand-authored skill linked)
   b. If yes: that skill is a CANDIDATE (high confidence — proven pattern)
   c. If no: the extension is a CAPABILITY (usable but no proven pattern)
6. For each CANDIDATE skill:
   a. Check `when_to_use` against the parsed intent — does any trigger match?
   b. Check `requires` against the active workspace profile + Workspace Context Graph state — are prerequisites met and in scope?
   c. If both pass: skill is SELECTED
7. SEQUENCE selected skills by resolving `requires.skills` dependencies
8. FILL GAPS: where the plan needs capabilities without exemplar skills,
   fall back to code generation grounded in the extension's SDK methods
```

### §24.6.1.A Suggestion-to-build rule

A suggestion is not a build until it passes four gates:

1. **Evidence gate**: it references at least one Workspace Context claim and at least one Capability Graph extension.
2. **Scope gate**: every referenced workspace object is inside the active workspace profile's allowed scope.
3. **Buildability gate**: at least one selected skill or generated-capability path can produce a concrete artifact.
4. **Approval gate**: any write-side action remains paused until the user accepts the plan or the configured policy marks the workspace as safe for automatic prototype writes.

The Console should therefore expose two separate actions:

- `Inspect suggestion`: view evidence, missing inputs, and expected artifacts.
- `Plan and build`: create an `outcome_execution`, run `stage:agent-design`, and continue through generate/validate/evaluate/deploy subject to approval gates.

### §24.6.1.B Skill Request Discovery During Execution

Use-case execution is allowed to discover missing skills, but it is not allowed to silently improvise them. When an execution discovers a missing tool, CLI, SDK installer, runtime isolation requirement, package conflict, connector, scorer, or approval policy, BrickVision emits a **Skill Request** and pauses the execution.

The Lakebridge proof produced the canonical example:

- Lakebridge required `databricks-sdk~=0.85.0`.
- The running BrickVision app used `databricks-sdk==0.106.0`.
- Installing Lakebridge into the main app venv would have downgraded the app runtime and risked breaking the Console/API.
- The correct move was to create an isolated `.lakebridge-venv`, install `databricks-labs-lakebridge==0.12.2` there, run bounded Lakebridge APIs, and capture artifacts.

That behavior must become a reusable skill, not a one-off terminal trick.

Skill Request protocol:

1. Skill execution detects a missing capability or unsafe runtime condition.
2. Execution transitions to `SKILL_REQUEST_DISCOVERED`.
3. A `SkillRequest` record is emitted with the requested skill/tool, evidence, reason code, suggested owner, and resumability contract.
4. Skill Library opens with a prefilled Skill Request and optional draft skeleton.
5. Partner or BrickVision author validates/signs the new skill or rejects the gap.
6. If accepted, the enabled skill pack version is recorded, the planner resumes from the paused execution, and the original use case continues.

The execution must not:

- install conflicting libraries into the app/runtime venv;
- bypass the Skill Library validation flow;
- continue a write-side action with an unvalidated tool;
- hide the gap by falling back to generic LLM code.

`SkillRequest` shape:

```yaml
request_id: skill_request:lakebridge-isolated-runtime
execution_id: outcome_exec:...
detected_at_stage: stage:agent-generate
request_kind: runtime
requested_skill_id: skill:runtime.isolated-python-package-execution
reason_code: DEPENDENCY_CONFLICT_WITH_APP_RUNTIME
evidence:
  package: databricks-labs-lakebridge==0.12.2
  required_dependency: databricks-sdk~=0.85.0
  app_dependency: databricks-sdk==0.106.0
impact: Main app environment would be downgraded if package were installed in-place.
suggested_resolution: Create isolated package execution skill and rerun Lakebridge step there.
resumable_from: skill_invocation:lakebridge_transpile
status: open
```

### §24.6.1.C Core requested skill: isolated Python package execution

The first core requested skill discovered by the Lakebridge proof is:

```yaml
id: skill:runtime.isolated-python-package-execution
title: Run a bounded Python package or CLI in an isolated runtime
exemplar_of: meta:workspace-administration/ext:create-isolated-runtime
capability_anchor_status: pending_indexer_stub
when_to_use:
  - A partner skill requires a Python package that conflicts with the app runtime
  - A CLI or SDK installer must run without mutating BrickVision's main environment
  - A migration, assessment, or codegen tool requires pinned dependencies
requires:
  tools:
    - tool:runtime.create_venv
    - tool:runtime.install_python_package
    - tool:runtime.run_bounded_command
    - tool:runtime.capture_artifacts
inputs:
  - name: python_version
    type: string
    required: true
  - name: packages
    type: string[]
    required: true
  - name: command_or_module
    type: string
    required: true
  - name: input_artifacts
    type: string[]
    required: false
outputs:
  - name: execution_report
    type: object
  - name: captured_artifacts
    type: string[]
  - name: dependency_lock
    type: object
eval:
  scorers:
    - IsolatedRuntimeNoMainEnvMutation
    - DependencyLockCaptured
    - BoundedExecutionTimeout
    - ArtifactCaptureCompleteness
constitutional:
  - no.mutate.main.app.environment
  - no.unbounded.network.access
  - no.production.write.without.approval
execution:
  runtime: local_or_serverless_isolated
  on_failure: emit_skill_gap_or_question
```

This skill is a prerequisite for robust partner accelerators. Any starter template that needs conflicting libraries, Databricks Connect, external CLIs, or source-system SDKs must depend on it instead of installing dependencies directly into BrickVision.

### §24.6.A ML Execution Strategy (NEW v0.7.9 — "no custom ML code")

**Principle:** BrickVision must never write custom ML training code. The ML lifecycle is decomposed into a chain of contracted skills, each retrieving grounded Databricks API evidence from the Capability Graph. This is the same architecture as SQL execution (skill chain → Statement Execution API) and PySpark execution (skill chain → Jobs API with `spark_python_task`).

**The ML skill chain (10 skills, executed sequentially):**

```
ml.problem-select          → What ML problem type? (classification, regression, clustering, etc.)
    │
ml.feature-readiness       → Are required features available and profiled?
    │
ml.strategy-plan           → What training strategy? (single model, ensemble, AutoML, etc.)
    │
ml.model-family-select     → Which model family? (linear, tree-ensemble, neural, etc.)
    │
ml.training-backend-probe  → What's available on this workspace's serverless runtime?
    │
ml.training-backend-select → Which backend will execute training? (MLflow, Feature Eng, etc.)
    │
ml.training-task-plan      → Generate the training task spec (code, deps, compute shape)
    │
ml.api-plan-bind           → Bind the plan to concrete Databricks API operations
    │
lakeflow.jobs-run-submit   → Submit as a serverless Databricks Job
    │
ml.train-evaluate-register → Evaluate results and register model in Unity Catalog
```

**Each skill's contract:**

| Skill | Input | Output | Retrieves from Graph |
|-------|-------|--------|---------------------|
| `ml.problem-select` | Outcome spec, data schema | `problem_type`, `target_column`, `metric` | `meta:mlflow-tracking/ext:ml-problem-type-selection` |
| `ml.feature-readiness` | Data source refs, schema | Feature availability report, missing columns | `meta:feature-engineering/ext:feature-readiness-check` |
| `ml.strategy-plan` | Problem type, data profile | Training strategy (single/ensemble/AutoML) | `meta:mlflow-tracking/ext:ml-strategy-plan` |
| `ml.model-family-select` | Strategy, data characteristics | Model family, hyperparameter space | `meta:mlflow-tracking/ext:model-family-selection` |
| `ml.training-backend-probe` | Workspace profile | Available ML libraries, compute constraints | `meta:mlflow-tracking/ext:training-backend-probe` |
| `ml.training-backend-select` | Probe results, model family | Selected backend, execution mode | `meta:mlflow-tracking/ext:training-backend-selection` |
| `ml.training-task-plan` | All above outputs | `spark_python_task` spec, generated training code | `meta:mlflow-tracking/ext:training-task-plan` |
| `ml.api-plan-bind` | Task plan | Concrete `DatabricksApiPlan` operations | `meta:mlflow-tracking/ext:api-plan-bind` |
| `lakeflow.jobs-run-submit` | API plan | Job run ID, execution status | `meta:lakeflow-jobs/ext:jobs-run-submit` |
| `ml.train-evaluate-register` | Run results | Model URI, metrics, UC registration | `meta:mlflow-tracking/ext:train-with-floor-and-register` |

**Why this replaces custom code:**

The previous implementation (`src/brickvision_runtime/ml/databricks_training.py`) attempted to write a monolithic training driver with handcoded if/else branches for different ML backends, library imports, and model families. This approach was fundamentally wrong because:

1. It duplicated knowledge that already exists in the Capability Graph (API signatures, SDK methods, model registration flows).
2. It required custom hotfixes every time a serverless runtime constraint was discovered (e.g., `pyspark.ml` unavailable, `databricks.automl` blocked).
3. It bypassed the skill contract system — no typed inputs/outputs, no eval scorers, no constitutional constraints.
4. It could not be extended by partners without forking BrickVision core.

The skill-chain approach means each step is independently testable, replaceable, and partner-extensible. A partner who wants a different model family selection strategy creates a `skill:partner.<id>.ml.model-family-select` that overrides the core skill via the skill resolution protocol (§24.6.1).

**Serverless constraint handling:** The `ml.training-backend-probe` skill is the evidence-gathering step. It runs a lightweight Python probe on the target serverless runtime to discover which ML libraries are actually importable (`sklearn`, `xgboost`, `lightgbm`, `mlflow`, `pandas`, `numpy`), what MLflow functionality works, and what data movement limits exist. The probe results are typed output, not if/else branches in training code. The `ml.training-backend-select` skill then maps (model-family, available-libraries) → (selected-backend, execution-mode) using the probe evidence, never guessing.

**Architecture lock:** No BrickVision contributor may add ML training logic outside the skill chain. If a new ML capability is needed, it enters as a new skill with a `SKILL.yaml` contract, `exemplar_of` linkage, and eval scorers. The `lakeflow.jobs-run-submit` skill is the only execution boundary — it submits code that was generated by skill plans, not handwritten.

**Source-grounding gate (May 14 repair item; validated May 15, 2026):** The ML, SQL, and PySpark execution chains may run only when their skill anchors are backed by indexed source evidence from the active Capability Graph snapshot. `exemplar_of` by itself is not enough. A skill whose anchor has only `source_kind=hand_authored` is a contract stub and must block with a grounded evidence gap, not fall back to custom code. The clean validation snapshot `snap_758295158481370` satisfies this gate for all 27 core skill anchors (`ungrounded=0`).

Required checks before any execution-boundary skill (`databricks.statement-execute`, `lakeflow.jobs-run-submit`, ML API bind/train/register) can proceed:

1. The active snapshot contains the skill's primary anchor in `extensions_synced`.
2. `source_provenance_synced` for that anchor includes at least one source-grounding kind: `sdk`, `openapi`, `docs`, `labs`, or `blog`.
3. The skill manifest has `capability_links.primary` containing `exemplar_of`.
4. Composite skills list supporting source-grounded extensions under `capability_links.uses`.
5. Missing support artifacts are emitted as explicit capability gaps; they are not guessed or substituted with custom skill code.

Validated status from active snapshot `snap_758295158481370`:

- `skill:databricks.statement-execute` is source-grounded and executed a non-destructive Statement Execution proof (`SELECT 1`).
- `skill:lakeflow.jobs-run-submit` is source-grounded and submitted a serverless PySpark validation job through the Jobs API (`run_id=55381123498710`).
- `skill:ml.api-plan-bind` reaches `ready` with a source-grounded Jobs submit operation and Statement Execution audit readback operation.
- Full ML training remains approval/artifact-gated: it must use a generated or partner-approved training artifact URI. BrickVision core must not add custom training-driver logic to bypass that gate.

### §24.6.2 The role of hand-authored skills

Hand-authored skills (BrickVision core PS/FDE skills plus validated partner skill packs) serve THREE purposes:

1. **High-confidence patterns**: When a hand-authored skill matches an outcome, the system can execute it with known-good contracts rather than generating code from scratch.
2. **Compositional building blocks**: The `requires.skills` field creates a composition graph — `harness.workspace-claim-emitter` composes 4 sub-skills into a meta-operation.
3. **Eval baselines**: Each skill's `eval.scorers` and `eval.golden_dataset` define what "correct execution" looks like — enabling Stage 3 (Validate) to measure quality objectively.

They do **not** serve as capability evidence. `hand_authored` provenance records who authored the contract and which extension it exemplifies; it must not raise source authority, satisfy API grounding, or make an executable skill ready when indexed SDK/OpenAPI/docs/labs evidence is missing.

### §24.6.3 Skill categories and their execution model

| Category | model_role | Execution | Examples |
|----------|-----------|-----------|----------|
| Mechanical | `null` | Direct tool calls, deterministic | `uc.catalog-introspect`, `lineage.introspect` |
| LLM-backed (codegen) | `pyspark_codegen`, `sql_codegen` | LLM generates → static validation → execute | `delta.pyspark-transform`, `delta.sql-transform` |
| LLM-backed (reasoning) | `skill_runtime_default`, `ml_recipe` | LLM reasons over KG → produces structured output | `uc.catalog-bootstrap-design`, `ml.train-evaluate-register` |
| Orchestrator (meta) | `null` | Composes sub-skills, manages lifecycle | `harness.workspace-claim-emitter` |

---

## §24.7 Tool Registry

Skills reference tools by ID (e.g., `tool:uc.list_catalogs`, `tool:ml.run_training_job`). The Tool Registry maps these IDs to executable SDK wrappers.

### §24.7.1 Tool contract

Each tool must declare:
- **ID**: e.g., `tool:uc.list_catalogs`
- **SDK binding**: the `databricks.sdk` method(s) it wraps
- **Input schema**: typed parameters
- **Output schema**: typed return
- **Side effects**: `read-only` | `create` | `modify` | `delete`
- **Permissions required**: UC grants needed
- **Idempotency**: whether repeated calls produce the same result

### §24.7.2 Tool categories (derived from SKILL.yaml `requires.tools`)

| Category | Tools Referenced | SDK Surface |
|----------|----------------|-------------|
| UC introspection | `tool:uc.list_catalogs`, `tool:uc.list_schemas`, `tool:uc.list_tables`, `tool:uc.list_views`, `tool:uc.list_volumes`, `tool:uc.list_functions` | `client.catalogs.list()`, `client.schemas.list()`, etc. |
| UC mutation | `tool:uc.bindings_check` | `client.workspace_bindings.*` |
| Data pipeline | `tool:data_pipeline.submit_sql`, `tool:data_pipeline.submit_pyspark_job` | Statement Execution API, Jobs API |
| ML lifecycle | `tool:ml.run_training_job`, `tool:ml.register_model` | MLflow + Jobs API |
| Runtime isolation | `tool:runtime.create_venv`, `tool:runtime.install_python_package`, `tool:runtime.run_bounded_command`, `tool:runtime.capture_artifacts` | Local/serverless isolated execution wrapper |
| KG operations | `tool:kg.emit_claims`, `tool:kg.upsert_freshness_belief` | Delta writes to KG tables |
| Harness meta | `tool:harness.invoke_skill`, `tool:harness.compute_changed_tables` | Internal skill invocation + Delta reads |

---

## §24.8 State Machine

Opportunity discovery and outcome execution move through well-defined states:

```
CONTEXT_REFRESHED → SUGGESTIONS_READY → SUGGESTION_ACCEPTED →
SUBMITTED → PLANNING → PLAN_READY →
  [APPROVAL_PENDING →] GENERATING → VALIDATING → EVALUATING →
  [APPROVAL_PENDING →] DEPLOYING → DEPLOYED
                                           │
  Any state may transition to:             ▼
  FAILED (unrecoverable)              COMPLETED
  PAUSED (awaiting human input)
  SKILL_REQUEST_DISCOVERED (awaiting Skill Library resolution)
  RETRYING (within budget)
```

### §24.8.1 State persistence

Each outcome execution is persisted in `<BV_CATALOG>.<BV_SCHEMA>.outcome_executions`:
- `execution_id` (primary key)
- `suggestion_id` (nullable; populated when execution starts from a Workspace Context suggestion)
- `outcome_spec` (JSON — the input)
- `state` (enum — current lifecycle state)
- `execution_plan` (JSON — the DAG produced by Stage 1)
- `skill_results` (JSON array — per-skill execution results)
- `validation_report` (JSON — Stage 3 output)
- `deployed_resources` (JSON array — Stage 5 output)
- `audit_trail` (JSON array — every state transition with timestamp + actor)
- `budget_consumed` (struct — tokens, tool_calls, wallclock)
- `created_at_ms`, `updated_at_ms`

Build suggestions are persisted separately in `<BV_CATALOG>.<BV_SCHEMA>.build_suggestions`:

- `suggestion_id` (primary key)
- `workspace_profile_id`, `workspace_id`, `config_hash`
- `title`, `category`, `confidence`, `estimated_complexity`
- `workspace_evidence_json`, `capability_evidence_json`
- `suggested_outcome_spec_json`
- `missing_inputs_or_questions_json`
- `status` (`open`, `dismissed`, `accepted`, `superseded`, `built`)
- `created_at_ms`, `accepted_at_ms`, `execution_id`

Skill requests are persisted separately in `<BV_CATALOG>.<BV_SCHEMA>.skill_requests`:

- `request_id` (primary key)
- `execution_id`, `suggestion_id`, `workspace_profile_id`
- `detected_at_stage`, `skill_invocation_id`
- `request_kind` (`skill`, `tool`, `runtime`, `package`, `connector`, `scorer`, `approval_policy`)
- `requested_skill_id`, `requested_tool_id`
- `reason_code`
- `evidence_json`
- `suggested_resolution_json`
- `resumable_from`
- `status` (`open`, `accepted`, `rejected`, `resolved`, `superseded`)
- `created_at_ms`, `resolved_at_ms`, `resolved_by_skill_pack_hash`

---

## §24.9 Budget Enforcement

Budget is enforced at three levels:

| Level | Mechanism | Enforced By |
|-------|-----------|-------------|
| **Per-skill** | `requires.budget.max_tool_calls`, `max_wallclock_sec`, `max_context_tokens` | The Skill Executor (Stage 2) |
| **Per-outcome** | Sum of selected skills' budgets, capped by partner config | Stage 1 (Architect) at plan time |
| **Per-day** | `BV_BUDGET_DAILY_TOKEN_CAP`, `BV_BUDGET_DAILY_COST_USD` | Global rate limiter |

On budget exhaustion:
- Per-skill: the skill is terminated, `on_failure` policy applied
- Per-outcome: execution transitions to `FAILED` with `BUDGET_EXHAUSTED` reason
- Per-day: new outcome submissions are queued until the next budget window

### §24.9.A BrickVision Observability UI (current direction)

The earlier generic Observability surface is no longer the right first product slice. BrickVision's current runtime has two high-value observable planes:

1. **LLM/model usage**: which configured Foundation Model endpoints BrickVision calls, why they were called, and how many input/output tokens they consumed.
2. **Execution health**: which skill/usecase/indexer runs are proven, blocked, failed, or waiting on approval/artifacts.

The first Observability UI should focus on LLM/model and token usage because that is where partner cost, governance, and replay questions concentrate.

#### Current configured LLM/model surfaces

BrickVision now intentionally uses only two model endpoint classes:

- `LLM_GENERAL_TASKS`: all non-embedding LLM work, including KG extraction, Knowledge answers, code generation, skill runtime reasoning, migration Code Convert / Lakebridge Switch, evaluation judging, and planning. The local default is `databricks-qwen3-next-80b-a3b-instruct`.
- `LLM_EMBEDDING_TASKS`: embedding work for capability graph / Vector Search materialization. The local default is `databricks-qwen3-embedding-0-6b`.

The earlier role-specific endpoint matrix (`BV_MODEL_ROLE_*`, `BV_KG_EXTRACTOR_ENDPOINT`, `BV_SWITCH_MODEL_ENDPOINT`, `DATABRICKS_MODEL_SERVING_ENDPOINT`) is no longer the product direction for local/dev operation. It created configuration drift without adding useful product clarity. If production later needs per-role routing, it must be reintroduced through an auditable model-routing table, not through a growing list of local env vars.

`BV_FAKE_LLM` remains a test-mode switch for canned responses; production Observability must identify fake/canned calls distinctly from real FMS calls.

Budget and token configuration currently exists in:

- `BV_INDEXER_DAILY_TOKEN_CAP`: hard cap for indexer LLM extraction/embedding work.
- `BV_INDEXER_DAILY_EMBEDDING_BUDGET_USD`: advisory embedding spend budget.
- `BV_BUDGET_NAMESPACE`: isolates app and indexer budget ledgers (`app` vs `indexer`).
- Skill `SKILL.yaml` budgets such as `max_total_input_tokens` and `max_total_output_tokens`.
- Local developer caps such as `BV_BUDGET_PER_BUILD_INPUT_TOKENS`, `BV_BUDGET_PER_BUILD_OUTPUT_TOKENS`, and `BV_BUDGET_DAILY_TOKENS`.

#### Current data gaps

The Observability UI must not imply telemetry exists before it is written. Current implementation gaps:

- `/api/knowledge/refresh-history` still surfaces `total_input_tokens: 0`; the service comments explicitly state token fields are placeholders until the indexer writes them.
- Capability-graph `RefreshPlanRow` stores `daily_token_cap` and `embedding_cost_usd`, while `CorpusHealthRow` stores `embedding_cost_usd_30d`, but neither stores per-model input/output token usage today.
- `capability_graph/llm.py` calls the configured `LLM_GENERAL_TASKS`, but does not persist request/response token counts, endpoint name, latency, or failure reason.
- The embedding path estimates token count for budget enforcement, but the UI does not yet expose token estimates, truncation, cache hit/miss counts, endpoint, or cost by run/source.
- The app-side SQL/PySpark/ML skill proof path persists execution status, but not LLM/model usage by skill invocation.

#### Required Observability UI panels

The initial BrickVision Observability UI should expose these panels:

| Panel | Shows | Backing data |
|---|---|---|
| Model usage by namespace | `app` vs `indexer`, endpoint class (`LLM_GENERAL_TASKS` or `LLM_EMBEDDING_TASKS`), call count, input tokens, output tokens, estimated cost, failures | New model invocation ledger |
| Token budget status | daily cap, used tokens, remaining tokens, advisory USD budget, 80% warning state, hard-cap breaches | Budget namespace config + invocation ledger |
| Indexer LLM/embedding usage | refresh run, source kind, general endpoint, embedding endpoint, cache hit/miss, estimated tokens, embedding cost, truncated-by-budget flag | `refresh_plan`, `corpus_health`, embedding result telemetry, invocation ledger |
| Usecase skill usage | usecase id/title, skill id, family, status, endpoint class if used, token totals, approval/artifact gate status | usecase proof tables + invocation ledger |
| Failure and gate reasons | reason code, user-visible explanation, next action, affected endpoint class | failures/questions + proof results |

#### New persistence contract

Add a flat UC table in the BrickVision schema:

`<BV_CATALOG>.<BV_SCHEMA>.model_invocation_ledger`

Required columns:

- `invocation_id`
- `namespace` (`app`, `indexer`, partner-defined)
- `source_surface` (`indexer`, `knowledge_search`, `usecase_detail`, `execution_monitor`, `skill_builder`, `cli`)
- `run_id`, `usecase_id`, `skill_id`, `execution_id`, `refresh_plan_id`
- `model_role`
- `endpoint_name`
- `request_kind` (`chat`, `embedding`, `structured_output`, `rerank`, `eval_judge`)
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `estimated_cost_usd`
- `latency_ms`
- `status` (`success`, `blocked_budget`, `failed`, `fake_llm`)
- `reason_code`
- `created_at_ms`
- `metadata_json`

Every real or fake LLM call must write one row. For local mode, writes can be best-effort; for staging/prod, missing ledger rows are a product bug because budget and replay cannot be audited.

#### Product rules

- Observability must distinguish **configured model roles** from **observed model calls**. A role can be configured without any usage.
- Token charts must never infer usage from budget caps alone.
- `fake_llm` usage must be visible and excluded from real spend totals.
- Indexer usage and app/usecase usage must be separable by namespace.
- The UI should show "not instrumented yet" for gaps instead of zero usage when the ledger is missing.

---

### §24.9.B BrickVision Evaluation UI and MLflow automation

Evaluation is a separate Console surface at `/evaluation`. Its job is to answer:

> "Can BrickVision's recommendations, retrieved evidence, generated artifacts, skill proofs, and operational decisions be trusted?"

Evaluation uses Databricks MLflow GenAI evaluation datasets stored in Unity Catalog. The dataset contract follows the MLflow schema:

- `inputs`: JSON-serializable application inputs, required.
- `expectations`: optional JSON-serializable ground truth or desired properties. Reserved keys used by MLflow/Databricks judges include `expected_facts`, `expected_response`, `guidelines`, and `expected_retrieved_context`.
- `source`: one provenance source per record: `human`, `document`, `trace`, or `synthetic`.
- `tags`: workflow, version, scenario, and gating metadata.

BrickVision maintains six first-class evaluation workflow families:

| Workflow | Evaluates | Example metrics |
|---|---|---|
| `capability_graph` | snapshot quality and capability retrieval | top-1 hit rate, SDK coverage, source grounding, duplicate key count, Lakebase sync verification |
| `hipporag2_retrieval` | search/ask retrieval and grounded generation | recall@k, document recall, answer groundedness, citation coverage, latency |
| `workspace_context` | workspace claims and build suggestions | claim freshness, profile completeness, suggestion acceptance, false-positive rate |
| `usecase_lifecycle` | candidate → artifact plan → validation → go/no-go | validation pass rate, blocker precision, decision accuracy, cycle time |
| `skill_execution` | SQL, PySpark, ML, AI proof execution | proof pass rate, runtime blocker recall, reproducibility, time to proof |
| `platform_cost` | quality of usage and budget instrumentation | missing-ledger detection, namespace isolation, cost per proven usecase, token budget status |

#### Automatic evaluation emission

Product-critical operations emit normalized evaluation events where the runtime
is already wired. This is separate from the model invocation ledger.

Implemented today:

- **Knowledge search/ask**: `rag_search` and `rag_answer` events from the
  Console API. `rag_answer` also logs a best-effort MLflow trace when
  `BV_MLFLOW_EVALUATION_EXPERIMENT_ID` is configured and `mlflow-skinny>=3.0`
  is installed in the Console API runtime.
- **Usecase and skill proof paths**: partial `usecase_stage` and `tool_proof`
  events with status, blockers, artifact pointers, and evidence.
- **Scorer runner**: `scorer_run` events for each registered dataset.

Planned but not complete:

- Indexer `indexer_snapshot` events at every promotion decision.
- Workspace-refresh and platform-cost runtime emitters.
- Execution-monitor trace capture for full dynamic workflow replay.
- Dataset-sync events.

Events are persisted in `<BV_CATALOG>.<BV_SCHEMA>.evaluation_events` with:

- `event_id`
- `event_kind` (`rag_search`, `rag_answer`, `usecase_stage`, `tool_proof`, `scorer_run`; `indexer_snapshot`, `workspace_refresh`, and `dataset_sync` are reserved/planned)
- `workflow`
- `status` (`observed`, `passed`, `failed`, `blocked`, `scored`)
- `subject_id` (`snapshot_id`, `usecase_id`, `execution_id`, `dataset_id`, or `query_hash`)
- `user_id`
- `mlflow_run_id`, `mlflow_trace_id`, `mlflow_dataset_name` (`rag_answer` and scorer runs can populate these now; search/usecase/tool-proof/indexer/workspace emitters still leave trace ids blank)
- `metrics_json`
- `inputs_json`
- `outputs_json`
- `expectations_json`
- `evidence_json`
- `reason_codes_json`
- `created_at_ms`

Writes are best-effort in local development and fail-open for user-facing API calls. In staging/prod, missing evaluation events for product-critical operations are defects because they break evaluation history.

#### Scoring model

Evaluation has three layers:

1. **Online event capture** records what happened immediately. These metrics are deterministic: latency, retrieved count, source count, blocker codes, execution status, cost estimates, and table/row counts.
2. **Live quality summaries** aggregate real `evaluation_events` over a time window. They show denominators such as event count, success count, failure count, trace coverage, and latency. They are the honest "what happened in real usage?" numbers.
3. **Offline scorer runs** evaluate quality against curated or trace-derived datasets. Scorers run on demand and on schedule, then write `scorer_run` rows back to UC for the Evaluation page.

The scorer runner loads datasets from `config/evaluation/evalsets.json`, syncs them with `mlflow.genai.datasets`, runs deterministic gates by default, and writes results to:

- `<BV_CATALOG>.<BV_SCHEMA>.evaluation_events` for event history.

The Evaluation UI renders MLflow run/trace links when these ids are present and
`DATABRICKS_HOST` plus `BV_MLFLOW_EVALUATION_EXPERIMENT_ID` are configured.

The runner also supports an opt-in MLflow GenAI Agent Evaluation pilot:
`scripts/run_evaluation_scorers.py --mlflow-genai-evaluate` or
`BV_EVALUATION_USE_MLFLOW_GENAI=true`. In that mode the runner builds
runtime-matched retrieval records from recent events, calls
`mlflow.genai.evaluate(...)`, and persists the returned MLflow run id on the
`scorer_run` event. The current live pilot is limited to HippoRAG2 answer
records because MLflow judge scorers expect answer-style `outputs`; Capability
Graph search remains deterministic retrieval scoring. This is not yet the
default scheduled gate because trace ids and replay wrappers are still
incomplete.

Live trace sampling is handled by `scripts/sample_live_evaluation_traces.py`.
It samples recent traced `rag_answer` events from `evaluation_events`, creates a
daily MLflow GenAI dataset with real inputs/outputs/evidence/status/reason
codes, and registers that dataset with `brickvision_dataset_source=live_trace_sample`.
These trace-sampled datasets are used for judge-style population evaluation and
failure triage. They must not be reported as curated gold-set accuracy unless a
human has added expectations.

The Databricks Asset Bundle declares the scheduled serverless Job
`bv_evaluation_scorers` for this runner. It runs after the indexer and
workspace-refresh windows by default, reads `config/evaluation/evalsets.json`,
runs curated scorer gates, materializes the latest live trace sample, and can be
triggered manually for pre-release quality gates.

The Console reads the latest `scorer_run` rows directly from
`evaluation_events`; there is no `evaluation_run_summaries` table yet.

#### Product rules

- Evaluation datasets must not be hardcoded in React, FastAPI handlers, or scorer code. Curated records live as versioned JSONL manifests and are synced into MLflow/UC datasets.
- Smoke queries are evaluation records, not hotfix logic. A smoke set can gate promotion only after it is represented as a MLflow/UC dataset with explicit `inputs`, `expectations`, `source`, and `tags`.
- The UI must never show a percentage without its denominator, time window, and source (`curated_regression` vs `live_events` vs `live_trace_sample`).
- Evaluation must cover both technical metrics and business-readable metrics. Business north stars include trusted recommendation rate, time to first executable proof, grounded answer rate, outcome proof rate, and cost per proven usecase.
- Evaluation and Observability can share raw telemetry but cannot share product semantics. Observability may show token spend; Evaluation decides whether that spend produced a correct, grounded, useful result.

---

## §24.10 Approval Gates

Approval gates pause execution at configured points and require human sign-off:

### §24.10.1 Gate triggers

1. **Constitutional violations detected**: If Stage 3 flags a potential violation of a skill's `constitutional` rules, execution pauses for review.
2. **Explicit outcome-level gates**: The outcome spec can declare `approval_gates` with `before:` conditions.
3. **Risk-threshold gates**: Actions with `side_effects: delete` or `side_effects: modify` on production resources trigger automatic approval gates.

### §24.10.2 Approval protocol

```
1. Execution pauses → state = APPROVAL_PENDING
2. ApprovalRequest emitted (contains: what action, why, what evidence, what risk)
3. Partner reviews via Console UI or programmatic API
4. Partner approves / rejects / requests modification
5. On approval: execution resumes from paused state
6. On rejection: execution transitions to FAILED with APPROVAL_REJECTED reason
```

---

## §24.11 Relationship to Capability Graph

The Capability Graph is the agent's **professional training**:

| Agent Need | Graph Provides |
|-----------|---------------|
| "What SDK method creates a cluster?" | Extension `meta:compute/ext:create` with chunk_text from SDK docs |
| "Is there a proven pattern for this?" | Hand-authored skill linked via `exemplar_skill_id` |
| "What's related to this capability?" | Entity edges (`derives`, `cites`) traversed via multi-hop PPR |
| "What authority backs this knowledge?" | Source provenance (sdk=1.00, docs=0.85, blog=0.50) |
| "What exists in THIS workspace?" | Workspace Context Graph (calculated by `workspace_context_refresh`, served from Lakebase synced tables) |

The three knowledge substrates are complementary:
- **Capability Graph** (indexed nightly) = what Databricks CAN do (universal)
- **Skill Knowledge Graph** (published from Skill Library lifecycle events) = what BrickVision and partner skills CAN do (implemented)
- **Workspace Context Graph** (refreshed by a config-scoped serverless Job) = what EXISTS here (contextual)

`stage:agent-design` consults all three when building an execution plan. The Capability Graph grounds "is this possible on Databricks"; the Skill Knowledge Graph grounds "which skill can do it"; the Workspace Context Graph grounds "what can it run against here" (existing tables, compute, permissions).

---

## §24.12 Implementation Phases

| Phase | What | Prerequisite | Effort |
|-------|------|-------------|--------|
| **A (done)** | Capability Graph + VS retrieval + code generation | — | Done (v0.7.8) |
| **B0** | Config Bundle Loader — parse `brickvision.yaml`, active `workspace_profile.yaml`, and `skill-packs.yaml`; compute content hashes; resolve secret references without storing secrets | A | 3-5 days |
| **B1** | Minimal Tool Registry + Workspace Context Graph first slice — read-only UC tools + `tool:kg.emit_claims` + `uc.catalog-introspect`, run by the serverless workspace context refresh Job and published to Lakebase `workspace_claims_current_synced` | B0 | 1-2 weeks |
| **B2** | Build Suggestions — read Workspace Context from Lakebase, ground candidates in Capability Graph, rank concrete build opportunities, expose them in the Workspace Context UI | B1 | 1 week |
| **B3** | Skill Request + isolated runtime foundation — persist `skill_requests`, add `skill:runtime.isolated-python-package-execution`, and make dependency conflicts pause/resume instead of mutating the app runtime | B1 | 1-2 weeks |
| **B4** | Partner Skill Pack Validator — validate pack manifests, namespace ownership, signatures, `exemplar_of`, `capability_links`, tool requirements, and enabled skill list | B0 | 1 week |
| **C** | Skill Executor — runtime that takes a validated SKILL.yaml + inputs and executes it | B1 + B3 + B4 | 2-3 weeks |
| **D** | Planner (stage:agent-design) — accepted suggestion or explicit outcome → execution plan decomposition against Databricks Capability Graph + Skill Knowledge Graph + Workspace Context Graph + enabled skill packs | B2 + C | 2-3 weeks |
| **E** | Multi-stage orchestration — the full 5-stage lifecycle with state machine | D | 3-4 weeks |
| **F** | Workspace Context Graph — implement `harness.workspace-claim-emitter` as first operational context-refresh skill | B1 | 2 weeks |
| **G** | Outcome Memory — store past builds, enable similar-outcome retrieval | E | 2 weeks |
| **H** | Approval Gates + Console UI for human-in-the-loop | E | 1-2 weeks |

**Minimum viable agent (B0 + B1 + B2 + B3 + B4 + C + D):** A single-pass system that loads a workspace profile, loads core + partner skill packs, introspects workspace state, suggests build opportunities, detects missing runtime/tool skills, lets the user choose or author a resolution, decomposes into skills, executes them, and returns results. No multi-stage validation, no production deployment without approval gates, no memory. ~9-13 weeks.

**Full operating model (all phases):** ~16-20 weeks for a team of 2-3 engineers.
