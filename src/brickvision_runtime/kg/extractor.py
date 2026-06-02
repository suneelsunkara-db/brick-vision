"""``kg_extractor`` invoker.

Per [`docs/23-databricks-capability-graph.md`](../../../docs/23-databricks-capability-graph.md)
§23.2.4 the capability_graph indexer's ``graph_builder`` task calls
the ``kg_extractor`` model role once per blog chunk to recover
``meta_skill`` mentions. This module is a *pure call wrapper* — no
orchestration, no fetch loops. The graph_builder wires the wrapper
into a per-chunk loop with a direct Foundation Model API call (see
:mod:`brickvision_runtime.capability_graph.llm`).

Contract (structured output schema):

```
{
  "mentions": [
    {
      "subject_id": "<entity-id>",
      "kind":       "<EntityKind value>",
      "evidence":   "<source span>",
      "confidence": 0.0..1.0
    },
    ...
  ],
  "vocabulary_gaps": [
     {"surface_form": "<text>", "context": "<snippet>"},
     ...
  ]
}
```

Returns ``(MentionExtractionResult, tuple[Question, ...])`` —
malformed rows become typed ``Question``s rather than silent
drops (P7).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterable
from typing import Any

from brickvision_runtime.failures import ReasonCode, question_from_failure
from brickvision_runtime.kg.predicates import EntityKind


@dataclasses.dataclass(frozen=True)
class Mention:
    """A single ``MENTIONS`` row staged for kg.emit_claims."""

    subject_id: str
    kind: EntityKind
    evidence: str
    confidence: float

    def as_claim_value(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "kind": self.kind.value,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


@dataclasses.dataclass(frozen=True)
class VocabularyGap:
    surface_form: str
    context: str


@dataclasses.dataclass(frozen=True)
class MentionExtractionResult:
    """Output of one ``kg_extractor`` invocation."""

    document_id: str
    chunk_id: str
    mentions: tuple[Mention, ...]
    vocabulary_gaps: tuple[VocabularyGap, ...]
    api_path: str
    routing_table_version_hash: str


# A coordinator is any callable that, given a request dict, returns a
# response dict. Production callers pass
# :func:`brickvision_runtime.capability_graph.llm.call_kg_extractor`;
# unit tests pass a stubbed callable.
CoordinatorCall = Callable[[dict[str, Any]], dict[str, Any]]


def kg_extractor(
    *,
    document_id: str,
    chunk_id: str,
    chunk_text: str,
    coordinator_call: CoordinatorCall,
    api_path: str = "v1/responses",
    routing_table_version_hash: str = "",
) -> tuple[MentionExtractionResult, tuple[Any, ...]]:
    """Invoke the ``kg_extractor`` model role on a single chunk.

    The returned ``Question[]`` carries any rows that failed
    validation — e.g. an unknown ``kind`` or a missing field.
    The caller (``skill:docs.lookup``) decides whether to emit
    a ``CONTENT`` claim anyway.
    """

    request = {
        "model_role": "kg_extractor",
        "document_id": document_id,
        "chunk_id": chunk_id,
        "chunk_text": chunk_text,
    }
    raw = coordinator_call(request)

    questions: list[Any] = []
    mentions = list(_validate_mentions(raw.get("mentions", []), questions))
    gaps = tuple(_validate_gaps(raw.get("vocabulary_gaps", []), questions))

    result = MentionExtractionResult(
        document_id=document_id,
        chunk_id=chunk_id,
        mentions=tuple(mentions),
        vocabulary_gaps=gaps,
        api_path=raw.get("api_path", api_path),
        routing_table_version_hash=(
            raw.get("routing_table_version_hash", routing_table_version_hash)
        ),
    )
    return result, tuple(questions)


def _validate_mentions(
    rows: Iterable[Any],
    questions: list[Any],
) -> Iterable[Mention]:
    for row in rows:
        if not isinstance(row, dict):
            questions.append(_q_unparseable(repr(row)))
            continue
        try:
            kind = EntityKind(row["kind"])
        except (KeyError, ValueError):
            questions.append(
                question_from_failure(
                    reason=ReasonCode.MENTION_PRECISION_BELOW_FLOOR,
                    subject=str(row.get("subject_id", "")),
                    raised_by="skill:docs.lookup",
                    details={"reason": "unknown_or_missing_kind", "row": row},
                )
            )
            continue
        try:
            yield Mention(
                subject_id=str(row["subject_id"]),
                kind=kind,
                evidence=str(row.get("evidence", "")),
                confidence=float(row.get("confidence", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            questions.append(_q_unparseable(json.dumps(row, sort_keys=True)))


def _validate_gaps(
    rows: Iterable[Any],
    questions: list[Any],
) -> Iterable[VocabularyGap]:
    for row in rows:
        if not isinstance(row, dict) or "surface_form" not in row:
            questions.append(_q_unparseable(repr(row)))
            continue
        yield VocabularyGap(
            surface_form=str(row["surface_form"]),
            context=str(row.get("context", "")),
        )


def _q_unparseable(payload: str) -> Any:
    return question_from_failure(
        reason=ReasonCode.MENTION_PRECISION_BELOW_FLOOR,
        subject="kg_extractor",
        raised_by="skill:docs.lookup",
        details={"reason": "unparseable_row", "payload": payload[:512]},
    )


__all__ = [
    "Mention",
    "MentionExtractionResult",
    "VocabularyGap",
    "kg_extractor",
]
