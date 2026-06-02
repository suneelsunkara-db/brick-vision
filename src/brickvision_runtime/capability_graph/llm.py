"""Direct Foundation Model API caller for the capability_graph kg_extractor role.

Replaces the deleted ``brickvision_runtime.harness.coordinator`` dependency. The
indexer Job needs exactly one LLM round-trip per blog chunk: extract structured
meta-skill mentions as a JSON object. Anything more elaborate belonged to the
build pipeline harness (deleted v0.7.7).

Environment:
    LLM_GENERAL_TASKS: Databricks Model Serving endpoint name.
    BV_FAKE_LLM:      When ``true``, callers route to canned fixtures
                      instead of invoking this module.

The indexer Job is the only consumer; tests pass ``canned=`` and never reach
this codepath.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from brickvision_runtime.observability import record_model_invocation

_KG_EXTRACTOR_SYSTEM_PROMPT = (
    "You extract meta-skill mentions from a Databricks blog chunk. "
    "Return STRICT JSON of shape "
    '{"mentions":[{"subject_id":"<meta_skill_id>","kind":"meta_skill",'
    '"evidence":"<short quote>","confidence":<0.0-1.0>}],"vocabulary_gaps":[]}'
    " — no prose, no markdown."
)


def call_kg_extractor(request: dict[str, Any]) -> dict[str, Any]:
    """Invoke the kg_extractor model role on a single chunk.

    ``request`` shape (per kg.extractor.kg_extractor): ``{"document_id", "chunk_id",
    "chunk_text", "candidate_meta_skills"}``. Returns ``{"mentions": [...],
    "vocabulary_gaps": [...]}`` or ``{"mentions": [], "vocabulary_gaps": []}``
    on failure.
    """

    endpoint = os.environ.get("LLM_GENERAL_TASKS", "databricks-qwen3-next-80b-a3b-instruct").strip()
    if not endpoint:
        return {"mentions": [], "vocabulary_gaps": []}

    chunk_text = str(request.get("chunk_text", ""))
    candidates = list(request.get("candidate_meta_skills", []))
    user_message = (
        f"Candidate meta-skill IDs: {candidates}\n\n"
        f"Chunk:\n{chunk_text}"
    )

    started_at_ms = int(time.time() * 1000)
    response: Any | None = None
    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415

        client = WorkspaceClient()
        response = client.serving_endpoints.query(
            name=endpoint,
            messages=[
                {"role": "system", "content": _KG_EXTRACTOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            max_tokens=512,
        )
    except Exception as exc:
        record_model_invocation(
            feature="indexer_kg_extraction",
            model_role="general_tasks",
            endpoint=endpoint,
            request_kind="chat",
            status="failed",
            started_at_ms=started_at_ms,
            error=exc,
            metadata={
                "document_id": str(request.get("document_id") or ""),
                "chunk_id": str(request.get("chunk_id") or ""),
                "candidate_count": len(candidates),
            },
        )
        return {"mentions": [], "vocabulary_gaps": []}
    record_model_invocation(
        feature="indexer_kg_extraction",
        model_role="general_tasks",
        endpoint=endpoint,
        request_kind="chat",
        status="succeeded",
        started_at_ms=started_at_ms,
        response=response,
        metadata={
            "document_id": str(request.get("document_id") or ""),
            "chunk_id": str(request.get("chunk_id") or ""),
            "candidate_count": len(candidates),
        },
    )

    choices = getattr(response, "choices", None) or []
    if not choices:
        return {"mentions": [], "vocabulary_gaps": []}
    content = getattr(choices[0].message, "content", "") if choices else ""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"mentions": [], "vocabulary_gaps": []}
    if not isinstance(parsed, dict):
        return {"mentions": [], "vocabulary_gaps": []}
    parsed.setdefault("mentions", [])
    parsed.setdefault("vocabulary_gaps", [])
    return parsed


__all__ = ["call_kg_extractor"]
