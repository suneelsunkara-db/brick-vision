"""Business usecase candidate compilation from evidence starters."""

from __future__ import annotations

from typing import Any

from .usecase_suggestions import list_workspace_build_suggestions


def list_usecase_candidates(*, user_id: str, limit: int = 12) -> dict[str, Any]:
    """Compile business-facing candidates from evidence-backed starters.

    This first slice is intentionally read-only. It does not persist usecases or
    pretend to infer a complete business workflow; it turns starter evidence into
    explicit candidate records with gaps the UI can show.
    """

    starters_payload = list_workspace_build_suggestions(user_id=user_id, limit=limit)
    starters = list(starters_payload.get("suggestions") or [])
    candidates = [
        candidate
        for starter in starters
        for candidate in _candidates_from_evidence_starter(starter)
    ]
    candidates.sort(
        key=lambda item: (
            _readiness_rank(str(item["readiness"])),
            -float(item["confidence"]),
            item["title"],
        )
    )

    return {
        "candidates": candidates[: max(1, min(int(limit), 50))],
        "active_snapshot_id": starters_payload.get("active_snapshot_id"),
        "indexer_state": starters_payload.get("indexer_state", "unknown"),
        "message": starters_payload.get("message"),
        "source": {
            "kind": "workspace_evidence_starters",
            "evidence_gate": starters_payload.get("evidence_gate"),
        },
    }


def _candidates_from_evidence_starter(starter: dict[str, Any]) -> list[dict[str, Any]]:
    target = starter.get("target") if isinstance(starter.get("target"), dict) else {}
    evidence_summary = (
        starter.get("evidence_summary")
        if isinstance(starter.get("evidence_summary"), dict)
        else {}
    )
    schema_ref = str(target.get("schema_ref") or "unknown schema")
    table_count = int(evidence_summary.get("table_count") or 0)
    row_count = int(evidence_summary.get("row_count") or 0)
    profiled_columns = int(evidence_summary.get("profiled_column_count") or 0)
    template_id = str(starter.get("template_id") or "")
    table_names = _table_names(starter)
    proposals = _proposal_specs(schema_ref=schema_ref, table_names=table_names)

    return [
        _candidate_from_proposal(
            starter=starter,
            proposal=proposal,
            template_id=template_id,
            schema_ref=schema_ref,
            table_count=table_count,
            row_count=row_count,
            profiled_columns=profiled_columns,
        )
        for proposal in proposals
    ]


def _candidate_from_proposal(
    *,
    starter: dict[str, Any],
    proposal: dict[str, Any],
    template_id: str,
    schema_ref: str,
    table_count: int,
    row_count: int,
    profiled_columns: int,
) -> dict[str, Any]:
    proposal_id = str(proposal["id"])
    evidence_refs = _proposal_evidence_refs(starter, proposal)
    confidence = _candidate_confidence(starter, table_count, row_count) + float(
        proposal.get("confidence_adjustment", 0.0)
    )
    return {
        "candidate_id": f"candidate:{proposal_id}:{_slug(schema_ref)}",
        "source_suggestion_id": starter.get("suggestion_id"),
        "title": proposal["title"],
        "status": "proposed",
        "readiness": _readiness(table_count, row_count, profiled_columns),
        "confidence": round(max(0.1, min(confidence, 0.92)), 2),
        "outcome": proposal["outcome"],
        "persona": proposal["persona"],
        "value_hypothesis": proposal["value_hypothesis"],
        "evidence_summary": (
            f"Proposed from {schema_ref}: {table_count} profiled tables, "
            f"{profiled_columns} profiled columns, {row_count} observed rows. "
            f"Key evidence: {', '.join(proposal['evidence_tables'][:6])}."
        ),
        "evidence_refs": evidence_refs,
        "evidence_tables": _proposal_evidence_tables(starter, proposal),
        "detected_entities": proposal.get("detected_entities", []),
        "build_paths": proposal.get("build_paths", []),
        "required_skill_families": proposal["skill_families"],
        "missing_inputs": proposal["missing_inputs"],
        "starter_artifacts": [
            {
                "kind": "technical_starter",
                "template_id": template_id or "unknown",
                "title": _artifact_title(proposal),
                "status": starter.get("status"),
                "target_ref": schema_ref,
            }
        ],
        "proposal_kind": proposal["kind"],
        "suggested_strategy": proposal["strategy"],
        "why_proposed": proposal["why_proposed"],
        "next_action": "Review this proposed usecase and decide whether to plan execution.",
    }


def _proposal_specs(*, schema_ref: str, table_names: set[str]) -> list[dict[str, Any]]:
    if schema_ref.endswith(".banking_ai_agent"):
        return _banking_ai_agent_proposals(table_names)
    if schema_ref.endswith(".test_partners"):
        return _partner_account_proposals(table_names)
    if schema_ref.endswith(".analysis_output"):
        return _analysis_output_proposals(table_names)
    return [_generic_profile_proposal(schema_ref, table_names)]


def _banking_ai_agent_proposals(table_names: set[str]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    if {"core_transactions", "enriched_transactions", "customer_profile"} <= table_names:
        proposals.append(
            {
                "id": "customer-spend-data-product",
                "kind": "data_modelling",
                "title": "Customer Spend Intelligence Data Product",
                "outcome": (
                    "Build a curated customer spend model that joins customer profile, "
                    "transactions, merchants, recurring expenses, and monthly summaries."
                ),
                "persona": "Banking analytics owner / data product owner",
                "value_hypothesis": (
                    "Give analysts and downstream apps one trusted customer-spend view "
                    "instead of rejoining raw transaction and enrichment tables."
                ),
                "why_proposed": (
                    "The schema has customer, transaction, merchant, monthly summary, "
                    "recurring expense, and insight tables with non-zero profile evidence."
                ),
                "detected_entities": [
                    "Customer",
                    "Account",
                    "Transaction",
                    "Merchant",
                    "Spend category",
                    "Recurring expense",
                    "Monthly spend summary",
                ],
                "build_paths": [
                    "Model a customer-level spend fact from transaction and enrichment tables.",
                    "Join merchant attributes and recurring-expense signals into a curated gold table.",
                    "Publish monthly spend aggregates for analyst, app, or ML consumption.",
                    "Add quality checks for transaction grain and merchant enrichment nulls.",
                ],
                "strategy": "composite",
                "evidence_tables": [
                    "customer_profile",
                    "core_transactions",
                    "enriched_transactions",
                    "dim_merchants",
                    "monthly_spending_summary",
                    "recurring_expenses",
                ],
                "skill_families": [
                    {"family": "SQL", "status": "available"},
                    {"family": "PySpark", "status": "recommended"},
                    {"family": "Deploy", "status": "needs_target"},
                ],
                "missing_inputs": [
                    "target data product owner",
                    "gold table naming convention",
                    "refresh cadence",
                    "consumer persona",
                ],
                "confidence_adjustment": 0.02,
            }
        )
    if {"recurring_expenses", "customer_profile", "enriched_transactions"} <= table_names:
        proposals.append(
            {
                "id": "recurring-expense-cancellation-classifier",
                "kind": "ml",
                "title": "Recurring Expense Cancellation Classifier",
                "outcome": (
                    "Train and evaluate a classification workflow that predicts whether "
                    "a recurring expense is cancellable from customer, merchant, and "
                    "spend behavior features."
                ),
                "persona": "Digital banking product owner / ML engineer",
                "value_hypothesis": (
                    "Help digital banking teams prioritize cancellable recurring expenses "
                    "with an explainable classifier grounded in observed customer spend data."
                ),
                "why_proposed": (
                    "The schema contains recurring expense records with cancellable_flag, "
                    "customer profiles, and enriched transaction context with non-zero "
                    "profile evidence."
                ),
                "detected_entities": [
                    "Customer",
                    "Recurring expense",
                    "Merchant",
                    "Spend behavior",
                    "Cancellable expense label",
                ],
                "build_paths": [
                    "Create feature inputs from recurring expenses, customer profiles, and enriched transactions.",
                    "Use cancellable_flag as the explicit classification target label.",
                    "Select a tabular classification model family and Databricks MLflow training backend.",
                    "Stop at the strategy approval and training artifact gates until a real Databricks artifact is bound.",
                ],
                "strategy": "ml",
                "evidence_tables": [
                    "recurring_expenses",
                    "customer_profile",
                    "enriched_transactions",
                ],
                "skill_families": [
                    {"family": "SQL", "status": "available"},
                    {"family": "PySpark", "status": "recommended"},
                    {"family": "ML", "status": "recommended"},
                    {"family": "Deploy", "status": "needs_target"},
                ],
                "missing_inputs": [
                    "strategy approval id",
                    "approved training artifact uri",
                    "model registry target",
                    "serving or batch-scoring target",
                ],
                "confidence_adjustment": 0.0,
            }
        )
    if {"advisory_sessions", "advisory_messages", "advisory_decisions"} <= table_names:
        proposals.append(
            {
                "id": "ai-advisory-agent-evaluation",
                "kind": "ai",
                "title": "AI Advisory Agent Evaluation and Feedback Loop",
                "outcome": (
                    "Create an agent-quality evaluation loop over advisory sessions, "
                    "messages, decisions, artifacts, and user feedback."
                ),
                "persona": "AI product owner / agent operations lead",
                "value_hypothesis": (
                    "Improve advisory-agent reliability by measuring recommendation "
                    "quality, tool usage, feedback, and validation outcomes."
                ),
                "why_proposed": (
                    "The schema contains advisory sessions/messages/decisions/artifacts "
                    "and user feedback, which are direct signals for AI-agent evaluation."
                ),
                "detected_entities": [
                    "Advisory session",
                    "Message turn",
                    "Agent decision",
                    "Generated artifact",
                    "User feedback",
                ],
                "build_paths": [
                    "Build an agent evaluation dataset from sessions, messages, decisions, and feedback.",
                    "Define success criteria for recommendation quality and tool-use behavior.",
                    "Measure missing validation states in advisory artifacts.",
                    "Create monitoring outputs for AI-agent operations review.",
                ],
                "strategy": "ai",
                "evidence_tables": [
                    "advisory_sessions",
                    "advisory_messages",
                    "advisory_decisions",
                    "advisory_artifacts",
                    "user_feedback",
                    "usecase_registry",
                ],
                "skill_families": [
                    {"family": "SQL", "status": "available"},
                    {"family": "AI", "status": "recommended"},
                    {"family": "ML", "status": "optional"},
                    {"family": "Deploy", "status": "needs_target"},
                ],
                "missing_inputs": [
                    "agent success criteria",
                    "gold evaluation examples",
                    "feedback quality rubric",
                    "monitoring destination",
                ],
                "confidence_adjustment": 0.01,
            }
        )
    return proposals or [_generic_profile_proposal("partner_demo_catalog.banking_ai_agent", table_names)]


def _partner_account_proposals(table_names: set[str]) -> list[dict[str, Any]]:
    if {"asean_account_list", "all_partner_attach_usecases"} <= table_names:
        return [
            {
                "id": "partner-attach-opportunity-model",
                "kind": "data_modelling",
                "title": "Partner Attach Opportunity Data Mart",
                "outcome": (
                    "Model account, geography, product-utilization, migration-source, "
                    "and implementation-stage data into a partner attach opportunity mart."
                ),
                "persona": "Partner sales leader / partner solutions architect",
                "value_hypothesis": (
                    "Prioritize partner accounts and attach motions from a trusted view "
                    "of accounts, products used, migration type, and target live date."
                ),
                "why_proposed": (
                    "The schema has account-list and partner attach usecase tables with "
                    "account ids, products utilized, migration fields, and sales impact."
                ),
                "detected_entities": [
                    "Partner account",
                    "Product utilization",
                    "Migration source",
                    "Implementation stage",
                    "Target live date",
                    "Sales impact",
                ],
                "build_paths": [
                    "Model accounts and attach opportunities into a partner-facing mart.",
                    "Create priority scoring from implementation stage, products used, and sales impact.",
                    "Flag migration-related opportunities by source platform and target live date.",
                ],
                "strategy": "composite",
                "evidence_tables": ["asean_account_list", "all_partner_attach_usecases"],
                "skill_families": [
                    {"family": "SQL", "status": "available"},
                    {"family": "PySpark", "status": "optional"},
                    {"family": "ML", "status": "candidate"},
                    {"family": "Deploy", "status": "needs_target"},
                ],
                "missing_inputs": [
                    "partner sales owner",
                    "priority scoring definition",
                    "target live date policy",
                    "dashboard or table target",
                ],
                "confidence_adjustment": 0.01,
            }
        ]
    return [_generic_profile_proposal("partner_demo_catalog.test_partners", table_names)]


def _analysis_output_proposals(table_names: set[str]) -> list[dict[str, Any]]:
    if "spending_knowledge_base" in table_names:
        return [
            {
                "id": "spending-knowledge-base-rag",
                "kind": "ai_candidate_blocked",
                "title": "Spending Knowledge Base RAG Candidate",
                "outcome": (
                    "Assess whether the spending knowledge base can support retrieval "
                    "for a customer-facing spend advisory assistant."
                ),
                "persona": "AI product owner / knowledge curator",
                "value_hypothesis": (
                    "Use curated spending knowledge to ground advisory responses, but "
                    "only after the source has non-empty indexed content."
                ),
                "why_proposed": (
                    "A knowledge-base-shaped table exists, but profile evidence shows "
                    "zero observed rows, so this is not ready to execute."
                ),
                "detected_entities": ["Knowledge base"],
                "build_paths": [
                    "Load or sync non-empty knowledge content before retrieval planning.",
                    "Profile document fields and chunking readiness once data exists.",
                    "Define retrieval quality criteria before building a RAG path.",
                ],
                "strategy": "ai",
                "evidence_tables": ["spending_knowledge_base"],
                "skill_families": [
                    {"family": "AI", "status": "blocked_by_empty_data"},
                    {"family": "SQL", "status": "available"},
                    {"family": "Deploy", "status": "not_ready"},
                ],
                "missing_inputs": [
                    "non-empty knowledge source",
                    "retrieval quality criteria",
                    "target assistant persona",
                ],
                "confidence_adjustment": -0.18,
            }
        ]
    return [_generic_profile_proposal("partner_demo_catalog.analysis_output", table_names)]


def _generic_profile_proposal(schema_ref: str, table_names: set[str]) -> dict[str, Any]:
    return {
        "id": "schema-profile-quality",
        "kind": "data_quality",
        "title": f"Schema Profile Quality Starter for {schema_ref}",
        "outcome": "Create a technical profile and quality-check starter from schema metadata.",
        "persona": "Data owner / platform engineer",
        "value_hypothesis": "Reduce downstream surprises by checking row counts, nulls, distincts, and candidate grains.",
        "why_proposed": "The schema has profile evidence but no stronger business pattern was detected from table names.",
        "detected_entities": sorted(table_names)[:8],
        "build_paths": [
            "Create row-count, null-count, distinct-count, and grain checks.",
            "Publish a technical starter artifact for schema profiling.",
        ],
        "strategy": "sql_only",
        "evidence_tables": sorted(table_names)[:8],
        "skill_families": [
            {"family": "SQL", "status": "available"},
            {"family": "PySpark", "status": "optional"},
            {"family": "Deploy", "status": "needs_target"},
        ],
        "missing_inputs": ["business owner", "quality thresholds", "deployment target"],
        "confidence_adjustment": -0.08,
    }


def _required_skill_families(template_id: str) -> list[dict[str, str]]:
    if template_id == "starter.schema-profile-quality":
        return [
            {"family": "SQL", "status": "available"},
            {"family": "PySpark", "status": "recommended"},
            {"family": "ML", "status": "not_required_yet"},
            {"family": "AI", "status": "not_required_yet"},
            {"family": "Deploy", "status": "missing"},
        ]
    return [
        {"family": "SQL", "status": "available"},
        {"family": "PySpark", "status": "missing"},
        {"family": "ML", "status": "unknown"},
        {"family": "AI", "status": "unknown"},
        {"family": "Deploy", "status": "missing"},
    ]


def _missing_inputs(template_id: str) -> list[str]:
    if template_id == "starter.schema-profile-quality":
        return [
            "business owner",
            "quality acceptance thresholds",
            "incident or monitoring workflow",
            "deployment target",
        ]
    return [
        "business owner",
        "value hypothesis",
        "acceptance criteria",
        "deployment target",
    ]


def _evidence_refs(starter: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    target = starter.get("target") if isinstance(starter.get("target"), dict) else {}
    schema_ref = target.get("schema_ref")
    if schema_ref:
        refs.append({"kind": "schema", "ref": schema_ref})
    for table in list(starter.get("included_tables") or [])[:8]:
        if isinstance(table, dict) and table.get("table_ref"):
            refs.append({"kind": "table", "ref": table["table_ref"]})
    return refs


def _table_names(starter: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for table in list(starter.get("included_tables") or []):
        if isinstance(table, dict):
            table_ref = str(table.get("table_ref") or "")
            if table_ref:
                names.add(table_ref.rsplit(".", 1)[-1])
    return names


def _proposal_evidence_refs(
    starter: dict[str, Any],
    proposal: dict[str, Any],
) -> list[dict[str, Any]]:
    target = starter.get("target") if isinstance(starter.get("target"), dict) else {}
    schema_ref = str(target.get("schema_ref") or "")
    refs: list[dict[str, Any]] = []
    if schema_ref:
        refs.append({"kind": "schema", "ref": schema_ref})
    evidence_tables = set(str(table) for table in proposal.get("evidence_tables", []))
    for table in list(starter.get("included_tables") or []):
        if not isinstance(table, dict):
            continue
        table_ref = str(table.get("table_ref") or "")
        table_name = str(table.get("table_name") or table_ref.rsplit(".", 1)[-1])
        if table_name in evidence_tables:
            refs.append({"kind": "table", "ref": table_ref})
    return refs


def _proposal_evidence_tables(
    starter: dict[str, Any],
    proposal: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence_tables = set(str(table) for table in proposal.get("evidence_tables", []))
    tables: list[dict[str, Any]] = []
    for table in list(starter.get("included_tables") or []):
        if not isinstance(table, dict):
            continue
        table_ref = str(table.get("table_ref") or "")
        table_name = str(table.get("table_name") or table_ref.rsplit(".", 1)[-1])
        if table_name not in evidence_tables:
            continue
        tables.append(
            {
                "table_name": table_name,
                "table_ref": table_ref,
                "row_count": int(table.get("row_count") or 0),
                "column_count": int(table.get("column_count") or 0),
                "profiled_column_count": int(table.get("profiled_column_count") or 0),
                "candidate_key_columns": list(table.get("candidate_key_columns") or []),
                "null_risk_columns": list(table.get("null_risk_columns") or []),
            }
        )
    tables.sort(key=lambda item: (-int(item["row_count"]), item["table_name"]))
    return tables


def _artifact_title(proposal: dict[str, Any]) -> str:
    kind = str(proposal.get("kind") or "")
    if kind == "data_modelling":
        return "Technical starter: curated SQL/PySpark data model"
    if kind == "ml":
        return "Technical starter: feature and training plan"
    if kind == "ai":
        return "Technical starter: agent evaluation dataset and metrics"
    if kind == "ai_candidate_blocked":
        return "Technical starter blocked: knowledge source has no rows"
    return "Technical starter: schema profiling and quality checks"


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _candidate_confidence(
    starter: dict[str, Any],
    table_count: int,
    row_count: int,
) -> float:
    confidence = float(starter.get("confidence") or 0.5) - 0.12
    if table_count >= 2:
        confidence += 0.04
    if row_count >= 100:
        confidence += 0.04
    return round(max(0.1, min(confidence, 0.86)), 2)


def _readiness(table_count: int, row_count: int, profiled_columns: int) -> str:
    if table_count > 0 and profiled_columns > 0 and row_count > 0:
        return "evidence_ready_skill_gaps"
    if table_count > 0 and profiled_columns > 0:
        return "evidence_partial"
    return "not_ready"


def _readiness_rank(readiness: str) -> int:
    ranks = {
        "evidence_ready_skill_gaps": 0,
        "evidence_partial": 1,
        "not_ready": 2,
    }
    return ranks.get(readiness, 99)
