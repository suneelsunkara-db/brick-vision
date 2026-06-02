"""Capability Graph RAG service."""

from __future__ import annotations

import os
import re
import time
from typing import Any

from .evaluation_events import emit_evaluation_event, query_hash
from .model_invocation_ledger import record_model_invocation


def search_capability_graph(
    *, user_id: str, query: str, limit: int = 10,
) -> dict[str, Any]:
    """Semantic search over the VS index — embed query, search, return chunks."""

    started_at_ms = int(time.time() * 1000)
    if not query.strip():
        _emit_rag_event(
            event_kind="rag_search",
            user_id=user_id,
            query=query,
            status="blocked",
            started_at_ms=started_at_ms,
            metrics={"limit": limit, "result_count": 0},
            outputs={"results": []},
            reason_codes=["EMPTY_QUERY"],
        )
        return {"results": [], "query": query}

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]

    client = WorkspaceClient()
    embedding_endpoint = os.environ.get(
        "LLM_EMBEDDING_TASKS", "databricks-qwen3-embedding-0-6b"
    )
    model_started_at_ms = int(time.time() * 1000)
    try:
        embed_resp = client.serving_endpoints.query(
            name=embedding_endpoint, input=[query.strip()]
        )
        record_model_invocation(
            feature="knowledge_search",
            model_role="embedding_tasks",
            endpoint=embedding_endpoint,
            request_kind="embedding",
            status="succeeded",
            started_at_ms=model_started_at_ms,
            user_id=user_id,
            metadata={"query_hash": query_hash(query), "limit": limit},
            response=embed_resp,
        )
    except Exception as exc:
        record_model_invocation(
            feature="knowledge_search",
            model_role="embedding_tasks",
            endpoint=embedding_endpoint,
            request_kind="embedding",
            status="failed",
            started_at_ms=model_started_at_ms,
            user_id=user_id,
            metadata={"query_hash": query_hash(query), "limit": limit},
            error=exc,
        )
        raise
    query_vector = list(embed_resp.data[0].embedding)

    from databricks.vector_search.client import VectorSearchClient  # type: ignore[import-not-found]

    catalog = os.environ.get("BV_CATALOG", "")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    vs_endpoint = os.environ.get("BV_VS_ENDPOINT", "bv_vs_endpoint")
    index_name = f"{catalog}.{schema}.entity_index"

    if not hasattr(search_capability_graph, "_vsc"):
        search_capability_graph._vsc = VectorSearchClient(  # type: ignore[attr-defined]
            disable_notice=True,
        )
    vsc = search_capability_graph._vsc  # type: ignore[attr-defined]
    index = vsc.get_index(endpoint_name=vs_endpoint, index_name=index_name)

    results = index.similarity_search(
        query_vector=query_vector,
        columns=[
            "id",
            "entity_id",
            "entity_kind",
            "chunk_text",
            "meta_skill_id",
            "top_order_id",
            "source_url",
        ],
        num_results=min(limit, 20),
    )

    hits = []
    if "result" in results and "data_array" in results["result"]:
        for row in results["result"]["data_array"]:
            hits.append(
                {
                    "id": row[0],
                    "entity_id": row[1],
                    "entity_kind": row[2],
                    "chunk_text": row[3] or "",
                    "meta_skill_id": row[4],
                    "top_order_id": row[5],
                    "source_url": row[6] or "",
                    "score": row[7] if len(row) > 7 else None,
                }
            )

    payload = {"results": hits, "query": query}
    _emit_rag_event(
        event_kind="rag_search",
        user_id=user_id,
        query=query,
        status="observed",
        started_at_ms=started_at_ms,
        metrics={
            "limit": limit,
            "result_count": len(hits),
            "source_count": len({str(item.get("source_url") or "") for item in hits if item.get("source_url")}),
        },
        outputs={"results": _event_safe_hits(hits)},
        evidence=_event_safe_hits(hits),
    )
    return payload


def _split_answer_and_code(full_response: str) -> tuple[str, str]:
    """Split an LLM response into explanation and code sections.

    Handles markdown-structured responses with ## Explanation / ## Code headers,
    or falls back to extracting fenced code blocks.
    """
    explanation = full_response
    code = ""

    # Try structured ## Explanation / ## Code split
    code_header = re.search(
        r"^##\s*Code\s*$",
        full_response,
        re.MULTILINE | re.IGNORECASE,
    )
    if code_header:
        explanation_part = full_response[:code_header.start()].strip()
        code_part = full_response[code_header.end():].strip()

        # Strip the ## Explanation header if present
        explanation_part = re.sub(
            r"^##\s*Explanation\s*\n*", "", explanation_part, flags=re.IGNORECASE
        ).strip()

        # Extract code from fenced blocks in the code section
        fenced = re.findall(r"```(?:python|py)?\s*\n(.*?)```", code_part, re.DOTALL)
        if fenced:
            code = "\n\n".join(block.strip() for block in fenced)
        else:
            code = code_part

        explanation = explanation_part
    else:
        # Fallback: extract all fenced python code blocks
        fenced = re.findall(r"```(?:python|py)?\s*\n(.*?)```", full_response, re.DOTALL)
        if fenced:
            code = "\n\n".join(block.strip() for block in fenced)
            # Remove code blocks from the explanation
            explanation = re.sub(
                r"```(?:python|py)?\s*\n.*?```",
                "",
                full_response,
                flags=re.DOTALL,
            ).strip()

    return explanation, code


def ask_capability_graph(
    *, user_id: str, question: str, top_k: int = 8,
) -> dict[str, Any]:
    """HippoRAG2-style RAG: retrieve → graph-expand → generate.

    1. Embed query and retrieve top-k chunks from Vector Search
    2. For each retrieved chunk, fetch 1-hop neighbors from entity_edges
       to expand context (graph walk)
    3. Assemble grounding context from retrieved + expanded chunks
    4. Generate answer via Foundation Model API (chat completion)
    """

    started_at_ms = int(time.time() * 1000)
    if not question.strip():
        _emit_rag_event(
            event_kind="rag_answer",
            user_id=user_id,
            query=question,
            status="blocked",
            started_at_ms=started_at_ms,
            metrics={"top_k": top_k, "chunks_retrieved": 0, "context_expanded": 0},
            outputs={"answer": "", "sources": []},
            reason_codes=["EMPTY_QUESTION"],
        )
        return {"answer": "", "sources": [], "question": question}

    # Step 1: Retrieve chunks via semantic search
    search_results = search_capability_graph(
        user_id=user_id, query=question, limit=top_k
    )
    hits = search_results.get("results", [])
    if not hits:
        _emit_rag_event(
            event_kind="rag_answer",
            user_id=user_id,
            query=question,
            status="failed",
            started_at_ms=started_at_ms,
            metrics={"top_k": top_k, "chunks_retrieved": 0, "context_expanded": 0},
            outputs={"answer": "No relevant information found", "sources": []},
            reason_codes=["NO_RETRIEVAL_HITS"],
        )
        return {
            "answer": "No relevant information found in the capability graph for this question.",
            "sources": [],
            "question": question,
        }

    # Step 2: Multi-hop PPR graph walk — expand context beyond direct neighbors
    expanded_context: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for hit in hits:
        if hit["id"] not in seen_ids:
            expanded_context.append(hit)
            seen_ids.add(hit["id"])

    schema = _bridge()._sanitize_ident(_bridge()._bv_schema())
    snapshot_row = _bridge()._active_snapshot()
    if snapshot_row and len(hits) >= 1:
        snapshot_id = snapshot_row[0]
        seed_ids = [h["entity_id"] for h in hits[:5] if h.get("entity_id")]

        # Multi-hop expansion using recursive CTE with PPR-style decay
        # alpha=0.15 teleport probability, max_depth=4 hops, budget=20 nodes
        if seed_ids:
            placeholders = ",".join(["%s"] * len(seed_ids))
            multi_hop_rows = _bridge()._query_all(
                f"""
                WITH RECURSIVE walk(entity_id, depth, score) AS (
                    SELECT unnest(ARRAY[{placeholders}]::text[]), 0, 1.0::double precision
                    UNION ALL
                    SELECT
                        CASE WHEN e.src_id = w.entity_id THEN e.dst_id ELSE e.src_id END,
                        w.depth + 1,
                        w.score * 0.85 * e.weight
                    FROM walk w
                    JOIN {schema}.entity_edges_synced e
                      ON e.snapshot_id = %s
                      AND (e.src_id = w.entity_id OR e.dst_id = w.entity_id)
                    WHERE w.depth < 4
                )
                SELECT entity_id, MAX(score) AS max_score
                FROM walk
                WHERE entity_id NOT IN ({placeholders})
                GROUP BY entity_id
                ORDER BY max_score DESC
                LIMIT 20
                """,
                (*seed_ids, snapshot_id, *seed_ids),
            )
            neighbor_entity_ids = [
                str(row[0]) for row in multi_hop_rows
                if row[0] and str(row[0]) not in seen_ids
            ]

            # Fetch chunk text for multi-hop neighbors from VS index
            if neighbor_entity_ids:
                try:
                    from databricks.sdk import (  # type: ignore[import-not-found]
                        WorkspaceClient as _WC,
                    )
                    from databricks.vectorsearch import (  # type: ignore[import-not-found]
                        VectorSearchClient as _VSC,
                    )

                    vs_index_name = os.environ.get(
                        "BV_VS_INDEX_NAME",
                        "brickvision.capability_graph.entity_index",
                    )
                    _vs_client = _VSC(
                        workspace_url=_WC().config.host,
                        token=_WC().config.token,
                    )
                    _vs_idx = _vs_client.get_index(index_name=vs_index_name)
                    for nid in neighbor_entity_ids[:15]:
                        if nid in seen_ids:
                            continue
                        try:
                            neighbor_result = _vs_idx.query(
                                columns=[
                                    "id",
                                    "entity_id",
                                    "entity_kind",
                                    "chunk_text",
                                    "meta_skill_id",
                                    "source_url",
                                ],
                                filters={"entity_id": nid},
                                num_results=1,
                            )
                            if (
                                neighbor_result
                                and neighbor_result.get("result", {}).get("data_array")
                            ):
                                cols = neighbor_result["result"]["column_names"]
                                row_data = neighbor_result["result"]["data_array"][0]
                                row_dict = dict(zip(cols, row_data))
                                if row_dict.get("chunk_text"):
                                    seen_ids.add(nid)
                                    expanded_context.append(row_dict)
                        except Exception:
                            pass
                except Exception:
                    pass

    # Step 3: Assemble grounding context
    context_parts: list[str] = []
    sources: list[dict[str, str]] = []
    for i, chunk in enumerate(expanded_context[:12]):
        text = chunk.get("chunk_text", "")
        if not text or text.startswith("[Related via"):
            continue
        kind = chunk.get("entity_kind", "unknown")
        meta = chunk.get("meta_skill_id", "") or ""
        context_parts.append(
            f"[Source {i+1} | {kind} | {meta}]\n{text}\n"
        )
        sources.append({
            "entity_id": chunk.get("entity_id", ""),
            "entity_kind": kind,
            "meta_skill_id": meta,
            "source_url": chunk.get("source_url", ""),
            "chunk_text_preview": text[:150],
        })

    grounding = "\n---\n".join(context_parts)

    # Step 4: Generate answer + executable code via Foundation Model API
    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]
    from databricks.sdk.service.serving import (  # type: ignore[import-not-found]
        ChatMessage,
        ChatMessageRole,
    )

    client = WorkspaceClient()
    chat_endpoint = os.environ.get(
        "LLM_GENERAL_TASKS", "databricks-qwen3-next-80b-a3b-instruct"
    )

    system_prompt = (
        "You are BrickVision, an expert Databricks engineer. You answer questions AND "
        "generate production-ready code grounded in the Databricks SDK and APIs.\n\n"
        "RULES:\n"
        "1. Use ONLY the retrieved evidence below to ground your response.\n"
        "2. Every SDK method or API call you reference MUST appear in the evidence.\n"
        "3. Structure your response in EXACTLY two sections:\n\n"
        "## Explanation\n"
        "A concise answer explaining the approach, citing source types "
        "(SDK method, docs pattern, API operation) and the relevant meta-skill.\n\n"
        "## Code\n"
        "Production-ready, runnable Python code that implements the answer. "
        "Use the `databricks-sdk` Python package. Include:\n"
        "- Correct imports from `databricks.sdk`\n"
        "- WorkspaceClient initialization\n"
        "- The actual API calls with realistic parameters\n"
        "- Error handling where appropriate\n"
        "- Brief inline comments explaining non-obvious steps\n\n"
        "If the question is purely conceptual (no code makes sense), "
        "write '# No executable code — this is a conceptual/architecture question' "
        "in the Code section.\n\n"
        "If the evidence is insufficient to generate correct code, say so "
        "clearly and show only what you CAN ground in evidence.\n\n"
        f"Retrieved evidence:\n{grounding}"
    )

    model_started_at_ms = int(time.time() * 1000)
    generation_error: Exception | None = None
    response: Any | None = None
    try:
        response = client.serving_endpoints.query(
            name=chat_endpoint,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM, content=system_prompt),
                ChatMessage(role=ChatMessageRole.USER, content=question),
            ],
            max_tokens=2048,
            temperature=0.1,
        )
        full_response = ""
        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            msg = getattr(choice, "message", None)
            if msg:
                full_response = getattr(msg, "content", "") or ""
        if not full_response:
            full_response = (
                str(getattr(response, "predictions", [""])[0])
                if hasattr(response, "predictions")
                else ""
            )
    except Exception as e:
        generation_error = e
        full_response = f"Generation failed: {type(e).__name__}: {e}"
    record_model_invocation(
        feature="knowledge_answer",
        model_role="general_tasks",
        endpoint=chat_endpoint,
        request_kind="chat",
        status="failed" if generation_error else "succeeded",
        started_at_ms=model_started_at_ms,
        user_id=user_id,
        response=response,
        error=generation_error,
        metadata={
            "question_hash": query_hash(question),
            "top_k": top_k,
            "chunks_retrieved": len(hits),
            "context_expanded": len(expanded_context),
        },
    )

    # Parse the response into explanation + code blocks
    explanation, code = _split_answer_and_code(full_response)

    payload = {
        "answer": explanation,
        "code": code,
        "full_response": full_response,
        "sources": sources,
        "question": question,
        "chunks_retrieved": len(hits),
        "context_expanded": len(expanded_context),
    }
    mlflow_trace = _log_rag_answer_trace(
        user_id=user_id,
        question=question,
        status="failed" if full_response.startswith("Generation failed:") else "observed",
        started_at_ms=started_at_ms,
        payload=payload,
        metrics={
            "top_k": top_k,
            "chunks_retrieved": len(hits),
            "context_expanded": len(expanded_context),
            "source_count": len(sources),
            "answer_length": len(explanation),
            "code_length": len(code),
        },
        evidence=sources,
    )
    _emit_rag_event(
        event_kind="rag_answer",
        user_id=user_id,
        query=question,
        status="observed" if not full_response.startswith("Generation failed:") else "failed",
        started_at_ms=started_at_ms,
        metrics={
            "top_k": top_k,
            "chunks_retrieved": len(hits),
            "context_expanded": len(expanded_context),
            "source_count": len(sources),
            "answer_length": len(explanation),
            "code_length": len(code),
        },
        outputs={
            "answer": explanation,
            "code_present": bool(code.strip()),
            "sources": sources,
        },
        evidence=sources,
        reason_codes=(
            ["GENERATION_FAILED"]
            if full_response.startswith("Generation failed:")
            else []
        ),
        mlflow_run_id=mlflow_trace.get("run_id", ""),
        mlflow_trace_id=mlflow_trace.get("trace_id", ""),
    )
    return payload




def _bridge() -> Any:
    from . import runtime_bridge

    return runtime_bridge


def _emit_rag_event(
    *,
    event_kind: str,
    user_id: str,
    query: str,
    status: str,
    started_at_ms: int,
    metrics: dict[str, Any],
    outputs: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    reason_codes: list[str] | None = None,
    mlflow_run_id: str = "",
    mlflow_trace_id: str = "",
) -> None:
    now_ms = int(time.time() * 1000)
    emit_evaluation_event(
        event_kind=event_kind,
        workflow="capability_graph" if event_kind == "rag_search" else "hipporag2_retrieval",
        status=status,
        subject_id=query_hash(query),
        user_id=user_id,
        metrics=metrics | {"latency_ms": max(0, now_ms - started_at_ms)},
        inputs={"query": query},
        outputs=outputs,
        evidence=evidence or [],
        reason_codes=reason_codes or [],
        mlflow_run_id=mlflow_run_id,
        mlflow_trace_id=mlflow_trace_id,
    )


def _log_rag_answer_trace(
    *,
    user_id: str,
    question: str,
    status: str,
    started_at_ms: int,
    payload: dict[str, Any],
    metrics: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, str]:
    """Best-effort MLflow trace for a Knowledge Ask response."""

    experiment_id = os.environ.get("BV_MLFLOW_EVALUATION_EXPERIMENT_ID", "").strip()
    if not experiment_id:
        return {}
    try:
        import mlflow  # type: ignore[import-not-found]

        _configure_mlflow_tracking(mlflow)
        mlflow.set_experiment(experiment_id=experiment_id)
        with mlflow.start_span(name="brickvision_knowledge_ask", span_type="CHAIN") as span:
            span.set_inputs({
                "question": question,
                "user_id": user_id,
                "question_hash": query_hash(question),
            })
            span.set_outputs({
                "answer": payload.get("answer", ""),
                "code_present": bool(str(payload.get("code") or "").strip()),
                "source_count": len(evidence),
                "status": status,
            })
            span.set_attributes({
                "workflow": "hipporag2_retrieval",
                "event_kind": "rag_answer",
                "latency_ms": max(0, int(time.time() * 1000) - started_at_ms),
                "metrics": metrics,
                "retrieved_sources": evidence,
            })
            trace_id = str(span.trace_id or "")
        mlflow.flush_trace_async_logging()
        run_id = _last_mlflow_run_id(mlflow)
        return {"trace_id": str(trace_id or ""), "run_id": run_id}
    except Exception:
        return {}


def _configure_mlflow_tracking(mlflow_module: Any) -> None:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if tracking_uri:
        mlflow_module.set_tracking_uri(tracking_uri)
    elif os.environ.get("DATABRICKS_HOST", "").strip():
        mlflow_module.set_tracking_uri("databricks")


def _last_mlflow_run_id(mlflow_module: Any) -> str:
    try:
        active_run = mlflow_module.active_run()
        if active_run and active_run.info:
            return str(active_run.info.run_id or "")
    except Exception:
        return ""
    return ""


def _event_safe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_hits: list[dict[str, Any]] = []
    for hit in hits[:20]:
        safe_hits.append(
            {
                "entity_id": str(hit.get("entity_id") or ""),
                "entity_kind": str(hit.get("entity_kind") or ""),
                "meta_skill_id": str(hit.get("meta_skill_id") or ""),
                "top_order_id": str(hit.get("top_order_id") or ""),
                "source_url": str(hit.get("source_url") or ""),
                "score": hit.get("score"),
            }
        )
    return safe_hits
