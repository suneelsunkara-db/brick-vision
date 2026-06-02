"""``/api/knowledge/*`` — Databricks Capability Graph read endpoints (v0.7.7).

7 read-only endpoints that back the ``/knowledge`` Console route's 5
tabs (Corpus, Top-Orders, Meta-Skills, Extensions, Refresh history)
plus the provenance drill-down pane.

Every endpoint reads from the Lakebase Autoscaling Postgres Synced
Tables produced by the indexer Job's T14 publish task (see
``docs/23-databricks-capability-graph.md`` §23.7 + service modules).
Read-only by construction; the indexer is the single writer, running
as ``bv_indexer_sp``. When Lakebase isn't configured yet (env vars
unset) or has no rows (publish hasn't completed), each endpoint
returns the SPA-safe empty payload + "indexer never run" banner that
the Knowledge tabs already render gracefully.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..identity import CurrentUser
from ..capability_graph_service import (
    get_capability_graph_corpus,
    get_capability_graph_health,
    get_capability_graph_refresh_history,
    get_extension_provenance,
    list_extensions,
    list_meta_skills,
    list_top_orders,
)
from ..capability_rag_service import (
    ask_capability_graph,
    search_capability_graph,
)
from ..evaluation_service import (
    get_evaluation_overview,
    list_evaluation_dataset_records,
)
from ..observability_service import (
    get_lakeflow_jobs_detail,
    get_model_serving_usage_detail,
    get_observability_overview,
    get_sql_queries_detail,
)
from ..usecase_service import (
    create_usecase_from_candidate,
    evaluate_usecase_go_no_go,
    generate_usecase_artifact_plan,
    get_migration_run,
    get_usecase_execution,
    get_usecase_skill_inputs,
    get_usecase_record,
    list_migration_runs,
    list_skill_builder_contracts,
    list_usecase_candidates,
    list_usecase_executions,
    list_usecase_tool_proofs,
    list_workspace_build_suggestions,
    plan_and_build_workspace_suggestion,
    resolve_usecase_skills,
    save_usecase_skill_inputs,
    save_usecase_inputs,
    save_usecase_strategy,
    start_migration_run,
    start_usecase_execution,
    run_usecase_tool_proof,
    validate_usecase_artifact_plan,
)
from ..workspace_context_service import (
    get_workspace_kg_summary,
    list_workspace_kg_claims,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/observability")
def get_observability(user: CurrentUser) -> dict[str, Any]:
    """Return platform observability facts for infra, jobs, LLMs, and usage."""

    return get_observability_overview(user_id=user.user_id)


@router.get("/observability/model-serving")
def get_observability_model_serving(
    user: CurrentUser,
    days: int = 7,
) -> dict[str, Any]:
    """Return filtered model-serving usage for BrickVision LLM endpoints."""

    return get_model_serving_usage_detail(days=days)


@router.get("/observability/jobs")
def get_observability_jobs(
    user: CurrentUser,
    days: int = 7,
) -> dict[str, Any]:
    """Return filtered BrickVision Lakeflow job runs."""

    return get_lakeflow_jobs_detail(days=days)


@router.get("/observability/sql")
def get_observability_sql(
    user: CurrentUser,
    hours: int = 24,
) -> dict[str, Any]:
    """Return filtered SQL warehouse query history."""

    return get_sql_queries_detail(hours=hours)


@router.get("/evaluation/overview")
def get_evaluation(user: CurrentUser) -> dict[str, Any]:
    """Return MLflow evaluation datasets and workflow readiness."""

    return get_evaluation_overview(user_id=user.user_id)


@router.get("/evaluation/datasets/{dataset_id}/records")
def get_evaluation_dataset_records(
    dataset_id: str,
    user: CurrentUser,
    limit: int = 25,
) -> dict[str, Any]:
    """Return a bounded preview of one registered MLflow evaluation dataset."""

    return list_evaluation_dataset_records(
        user_id=user.user_id,
        dataset_id=dataset_id,
        limit=limit,
    )


@router.get("/corpus")
def get_corpus(user: CurrentUser) -> dict[str, Any]:
    """List the 5 corpus sources with per-source health + freshness.

    Each row carries: ``source_id`` (sdk | openapi | docs | blog |
    labs), ``url_root``, ``source_authority`` (1.0 | 0.9 | 0.7 |
    0.5 | 0.6), ``last_refresh_ts``, ``state`` (ok | partial |
    failed), and the count of contributing Extensions.
    """

    return get_capability_graph_corpus(user_id=user.user_id)


@router.get("/top-orders")
def get_top_orders(user: CurrentUser) -> list[dict[str, Any]]:
    """List the 7 Top-Orders with hand-authored exemplar coverage badge.

    Coverage badge per ``docs/23-databricks-capability-graph.md``
    §23.2.6: a number indicating how many of the Top-Order's Meta-Skills
    have at least one hand-authored Extension exemplar. Top-Orders 3
    (Data Modelling) and 5 (Migration & Ingestion) ship at 0 in v0.7.7.
    """

    return list_top_orders(user_id=user.user_id)


@router.get("/meta-skills")
def get_meta_skills(
    user: CurrentUser,
    top_order: str | None = None,
) -> list[dict[str, Any]]:
    """List Meta-Skills, optionally filtered by parent Top-Order.

    A Meta-Skill is a Databricks capability area like ``meta:delta-lake``
    or ``meta:unity-catalog-foundation``. ~54 of these in v0.7.7 ship,
    each linked to exactly one Top-Order parent.
    """

    return list_meta_skills(user_id=user.user_id, top_order=top_order)


@router.get("/extensions")
def get_extensions(
    user: CurrentUser,
    meta_skill: str | None = None,
    has_exemplar: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List Extensions, optionally filtered by parent Meta-Skill or exemplar status.

    An Extension is a fine-grained Databricks capability under a
    Meta-Skill (e.g., ``ext:introspect-table-metadata`` under
    ``meta:delta-lake``). Each ``ext:`` is derived empirically from the
    SDK methods + REST operations + docs sections + blog posts +
    Lakebridge modules per ``docs/23-databricks-capability-graph.md``
    §23.2.4.
    """

    return list_extensions(
        user_id=user.user_id,
        meta_skill=meta_skill,
        has_exemplar=has_exemplar,
        limit=limit,
        offset=offset,
    )


@router.get("/extensions/provenance")
def get_provenance(extension_id: str, user: CurrentUser) -> dict[str, Any]:
    """Drill-down: which corpus chunks supplied this Extension's evidence.

    Returns the contributing chunk pointers (URL + line range / class
    name) per source, plus the kg_extractor output that produced the
    Extension row. Replay-pinned via ``capability_graph_snapshot_id``.

    Extension IDs contain slashes (e.g. ``meta:compute/ext:list``), so
    the ID is passed as a query parameter rather than a path segment.
    """

    return get_extension_provenance(
        user_id=user.user_id, extension_id=extension_id
    )


@router.get("/refresh-history")
def get_refresh_history(
    user: CurrentUser,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Last N capability-graph indexer refreshes.

    Each row: ``run_id``, ``started_at_ms``, ``ended_at_ms``,
    ``snapshot_id``, ``state`` (ok | rejected | partial | failed),
    ``rejection_reason_code`` (when state=rejected, e.g.,
    ``CAPABILITY_GRAPH_SMOKE_REGRESSION``), token spend, partial
    sources list.
    """

    return get_capability_graph_refresh_history(
        user_id=user.user_id, limit=limit
    )


@router.get("/health")
def get_health(user: CurrentUser) -> dict[str, Any]:
    """Capability-graph health rollup for the SPA's status banner.

    Surfaces freshness vs. ``BV_INDEXER_FRESHNESS_TOLERANCE_DAYS``
    (default 2), partial-source warnings, the locked v1 smoke baseline
    pass-rate, and the ``criterion 13`` status from
    ``docs/02-bet-and-principles.md`` §3.
    """

    return get_capability_graph_health(user_id=user.user_id)


@router.get("/workspace/summary")
def get_workspace_summary(user: CurrentUser) -> dict[str, Any]:
    """Workspace KG summary from Lakebase synced current-state claims."""

    return get_workspace_kg_summary(user_id=user.user_id)


@router.get("/workspace/claims")
def get_workspace_claims(
    user: CurrentUser,
    q: str = "",
    subject_kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List/search Workspace KG claims from ``workspace_claims_current_synced``."""

    return list_workspace_kg_claims(
        user_id=user.user_id,
        q=q,
        subject_kind=subject_kind,
        limit=limit,
        offset=offset,
    )


@router.get("/workspace/build-suggestions")
def get_workspace_build_suggestions(
    user: CurrentUser,
    limit: int = 12,
) -> dict[str, Any]:
    """Compile evidence-backed starter suggestions from Workspace KG."""

    return list_workspace_build_suggestions(user_id=user.user_id, limit=limit)


@router.get("/usecases/candidates")
def get_usecase_candidates(
    user: CurrentUser,
    limit: int = 12,
) -> dict[str, Any]:
    """Compile business usecase candidates from evidence-backed starters."""

    return list_usecase_candidates(user_id=user.user_id, limit=limit)


@router.get("/skill-builder/skills")
def get_skill_builder_skills(user: CurrentUser) -> dict[str, Any]:
    """List real Skill Builder contracts from checked-in SKILL.yaml files."""

    return list_skill_builder_contracts(user_id=user.user_id)


@router.post("/usecases")
def create_usecase(
    user: CurrentUser,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Persist a selected candidate as a draft usecase."""

    return create_usecase_from_candidate(
        user_id=user.user_id,
        candidate_id=str(body.get("candidate_id", "")),
    )


@router.get("/usecases/{usecase_id}")
def get_usecase(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Load a persisted draft usecase."""

    return get_usecase_record(user_id=user.user_id, usecase_id=usecase_id)


@router.post("/usecases/{usecase_id}/inputs")
def update_usecase_inputs(
    usecase_id: str,
    user: CurrentUser,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Append acceptance criteria and missing-input values for a draft usecase."""

    criteria = body.get("acceptance_criteria")
    if not isinstance(criteria, list):
        criteria = []
    values = body.get("missing_input_values")
    if not isinstance(values, dict):
        values = {}
    return save_usecase_inputs(
        user_id=user.user_id,
        usecase_id=usecase_id,
        acceptance_criteria=[str(item) for item in criteria],
        missing_input_values={str(key): value for key, value in values.items()},
    )


@router.post("/usecases/{usecase_id}/strategy")
def update_usecase_strategy(
    usecase_id: str,
    user: CurrentUser,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Append a selected strategy for a draft usecase."""

    return save_usecase_strategy(
        user_id=user.user_id,
        usecase_id=usecase_id,
        strategy_kind=str(body.get("strategy_kind", "")),
        rationale=str(body.get("rationale", "")),
    )


@router.get("/usecases/{usecase_id}/skills")
def get_usecase_skills(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Resolve concrete skill-family statuses for a draft usecase."""

    return resolve_usecase_skills(user_id=user.user_id, usecase_id=usecase_id)


@router.get("/usecases/{usecase_id}/skill-inputs")
def get_usecase_skill_input_requirements(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Return required input bindings for resolved existing skills."""

    return get_usecase_skill_inputs(user_id=user.user_id, usecase_id=usecase_id)


@router.post("/usecases/{usecase_id}/skill-inputs")
def update_usecase_skill_inputs(
    usecase_id: str,
    user: CurrentUser,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Append input bindings for one resolved skill family."""

    inputs = body.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    return save_usecase_skill_inputs(
        user_id=user.user_id,
        usecase_id=usecase_id,
        family=str(body.get("family", "")),
        skill_id=str(body.get("skill_id", "")),
        inputs={str(key): value for key, value in inputs.items()},
    )


@router.post("/usecases/{usecase_id}/artifact-plan")
def create_usecase_artifact_plan(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Create a draft artifact plan from selected strategy and bound inputs."""

    return generate_usecase_artifact_plan(user_id=user.user_id, usecase_id=usecase_id)


@router.post("/usecases/{usecase_id}/artifact-plan/validate")
def validate_artifact_plan(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Validate the latest artifact plan before execution."""

    return validate_usecase_artifact_plan(user_id=user.user_id, usecase_id=usecase_id)


@router.post("/usecases/{usecase_id}/evaluation")
def evaluate_usecase(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Evaluate whether the validated usecase can proceed to execution."""

    return evaluate_usecase_go_no_go(user_id=user.user_id, usecase_id=usecase_id)


@router.get("/usecases/{usecase_id}/tool-proofs")
def get_usecase_tool_proofs(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Return executable tool proof status for SQL, PySpark, ML, and AI."""

    return list_usecase_tool_proofs(user_id=user.user_id, usecase_id=usecase_id)


@router.post("/usecases/{usecase_id}/tool-proofs/{family}")
def run_tool_proof(
    usecase_id: str,
    family: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Run one executable tool proof for the selected usecase."""

    return run_usecase_tool_proof(
        user_id=user.user_id,
        usecase_id=usecase_id,
        family=family,
    )


@router.get("/usecases/{usecase_id}/executions")
def get_usecase_executions(
    usecase_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Return in-app execution monitor runs for a usecase."""

    del user
    return list_usecase_executions(usecase_id=usecase_id)


@router.post("/usecases/{usecase_id}/executions/{family}")
def start_execution_monitor_run(
    usecase_id: str,
    family: str,
    user: CurrentUser,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start one usecase proof execution without blocking the UI."""

    return start_usecase_execution(
        user_id=user.user_id,
        usecase_id=usecase_id,
        family=family,
        execution_inputs=payload,
    )


@router.get("/usecases/{usecase_id}/executions/{execution_id}")
def get_execution_monitor_run(
    usecase_id: str,
    execution_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Return one in-app execution monitor run."""

    del user
    return get_usecase_execution(usecase_id=usecase_id, execution_id=execution_id)


@router.get("/migration-runs")
def get_migration_runs(
    user: CurrentUser,
    usecase_id: str | None = None,
) -> dict[str, Any]:
    """Return persisted migration workflow runs."""

    return list_migration_runs(user_id=user.user_id, usecase_id=usecase_id)


@router.post("/migration-runs")
def create_migration_run(
    user: CurrentUser,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start one migration workflow run."""

    body = payload or {}
    inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    return start_migration_run(
        user_id=user.user_id,
        workflow_type=str(body.get("workflow_type") or "code_convert"),
        usecase_id=str(body.get("usecase_id") or "") or None,
        inputs=inputs,
    )


@router.get("/migration-runs/{migration_run_id}")
def get_one_migration_run(
    migration_run_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Return one migration workflow run."""

    del user
    return get_migration_run(migration_run_id=migration_run_id)


@router.post("/workspace/build-suggestions/{suggestion_id}/plan-and-build")
def plan_workspace_build_suggestion(
    suggestion_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Plan the deterministic artifact for an evidence-backed suggestion."""

    return plan_and_build_workspace_suggestion(
        user_id=user.user_id, suggestion_id=suggestion_id,
    )


@router.get("/search")
def search(
    user: CurrentUser,
    q: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Semantic search over the capability graph via Vector Search.

    Embeds the query, searches the VS index, and returns matching
    chunks with text, entity metadata, and similarity scores.
    """

    return search_capability_graph(user_id=user.user_id, query=q, limit=limit)


@router.post("/ask")
def ask(
    user: CurrentUser,
    body: dict[str, Any] = {},  # noqa: B006
) -> dict[str, Any]:
    """RAG endpoint: retrieve from capability graph + generate answer.

    HippoRAG2-style: embed query → VS retrieval → graph walk for
    context expansion → LLM generation grounded in retrieved evidence.

    Request body: ``{"question": "...", "top_k": 8}``
    """

    question = body.get("question", "")
    top_k = min(body.get("top_k", 8), 20)
    return ask_capability_graph(user_id=user.user_id, question=question, top_k=top_k)
