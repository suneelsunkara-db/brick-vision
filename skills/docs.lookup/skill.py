"""LLM-backed Layer-0 skill: ``skill:docs.lookup`` (N119).

Pipeline:
    fetch -> chunk (deterministic) -> emit CONTENT Claims
          -> kg_extractor per chunk -> emit MENTIONS Claims OR Questions

The skill is wired as ``Skill.llm_with_tools`` because the per-chunk
extractor call is the LLM half. The fetch + chunk + emit halves are
mechanical (DAG steps), but the orchestration is led by the LLM call
budget so we use the ``llm_with_tools`` shape.
"""

from __future__ import annotations

# bv:templated:start id=imports
from collections.abc import Callable, Iterable
from typing import Any

from brickvision_runtime.harness import (
    BehaviorConstraints,
    Skill,
    SystemPromptSection,
)
from brickvision_runtime.kg.extractor import (
    Mention,
    MentionExtractionResult,
    kg_extractor,
)
from brickvision_runtime.kg.predicates import Predicate

from .tools import Chunk, build_content_claim_values, chunk_document, hash_url_to_document_id
# bv:templated:end id=imports


# bv:templated:start id=prompt_sections
SYSTEM_PROMPT_SECTIONS: list[SystemPromptSection] = [
    SystemPromptSection(
        id="role",
        altitude="high",
        text="You extract entity mentions from a Databricks documentation chunk.",
    ),
    SystemPromptSection(
        id="schema",
        altitude="high",
        text=(
            "Return JSON {mentions: [{subject_id, kind, evidence, confidence}], "
            "vocabulary_gaps: [{surface_form, context}]}. kind must be one of "
            "the EntityKind enum values."
        ),
    ),
    SystemPromptSection(
        id="discipline",
        altitude="medium",
        text=(
            "If a surface form is unclear, surface it as a vocabulary_gap "
            "rather than guessing."
        ),
    ),
]
# bv:templated:end id=prompt_sections


# bv:templated:start id=skill
SKILL = Skill.llm_with_tools(
    id="skill:docs.lookup",
    version="0.1.0",
    model_role="kg_extractor",
    system_prompt_sections=SYSTEM_PROMPT_SECTIONS,
    tool_pool=["tool:docs.fetch_url", "tool:kg.emit_claims"],
    behavior_constraints=BehaviorConstraints(
        must_emit_evidence_chain=True,
        extra={
            "must_emit_content_before_mentions": True,
            "vocabulary_gaps_become_questions": True,
        },
    ),
    max_turns=8,
    constitutional=("no.write.to.uc", "respect.docs.fetch.rate.limit"),
)
# bv:templated:end id=skill


# bv:templated:start id=runner
def run_docs_lookup(
    *,
    url: str,
    fetch_url: Callable[[str], str],
    coordinator_call: Callable[[dict[str, Any]], dict[str, Any]],
    document_id: str | None = None,
    chunk_size_tokens: int = 800,
    chunk_overlap_tokens: int = 80,
    api_path: str = "v1/responses",
    routing_table_version_hash: str = "",
) -> dict[str, Any]:
    """Run the full pipeline.

    ``fetch_url`` and ``coordinator_call`` are dependency-injected so
    unit tests run offline. The output is a dict with three keys:

    - ``content_claims``  — list of ``CONTENT`` claim value dicts.
    - ``mention_claims``  — list of ``MENTIONS`` claim value dicts.
    - ``questions``       — typed Questions (vocab gaps + unparseable rows).
    """

    doc_id = document_id or hash_url_to_document_id(url)
    body = fetch_url(url)
    chunks = chunk_document(
        document_id=doc_id,
        text=body,
        chunk_size_tokens=chunk_size_tokens,
        chunk_overlap_tokens=chunk_overlap_tokens,
    )

    content_claims = [
        {
            "subject": doc_id,
            "predicate": Predicate.CONTENT.value,
            "value": v,
        }
        for v in build_content_claim_values(chunks)
    ]

    mention_claims: list[dict[str, Any]] = []
    questions: list[Any] = []

    for chunk in chunks:
        result, qs = kg_extractor(
            document_id=doc_id,
            chunk_id=chunk.chunk_id,
            chunk_text=chunk.chunk_text,
            coordinator_call=coordinator_call,
            api_path=api_path,
            routing_table_version_hash=routing_table_version_hash,
        )
        mention_claims.extend(_mentions_to_claim_values(result, chunk))
        questions.extend(qs)
        questions.extend(_vocab_gap_questions(result, chunk))

    return {
        "document_id": doc_id,
        "content_claims": content_claims,
        "mention_claims": mention_claims,
        "questions": questions,
        "chunk_count": len(chunks),
    }


def _mentions_to_claim_values(
    result: MentionExtractionResult,
    chunk: Chunk,
) -> Iterable[dict[str, Any]]:
    for m in result.mentions:
        yield {
            "subject": m.subject_id,
            "predicate": Predicate.MENTIONS.value,
            "value": _mention_value(m, chunk),
            "evidence_uris": (chunk.chunk_id,),
            "confidence": m.confidence,
        }


def _mention_value(m: Mention, chunk: Chunk) -> dict[str, Any]:
    return {
        **m.as_claim_value(),
        "document_id": chunk.document_id,
        "chunk_id": chunk.chunk_id,
    }


def _vocab_gap_questions(
    result: MentionExtractionResult,
    chunk: Chunk,
) -> Iterable[Any]:
    from brickvision_runtime.failures import question_from_failure
    from brickvision_runtime.failures import ReasonCode

    for gap in result.vocabulary_gaps:
        yield question_from_failure(
            reason=ReasonCode.MENTION_PRECISION_BELOW_FLOOR,
            subject=chunk.chunk_id,
            raised_by="skill:docs.lookup",
            details={
                "reason": "vocabulary_gap",
                "surface_form": gap.surface_form,
                "context": gap.context,
            },
        )
# bv:templated:end id=runner


__all__ = ["SKILL", "SYSTEM_PROMPT_SECTIONS", "run_docs_lookup"]
