"""Source adapters for the v0.7.7 Capability Graph (5 sources, §23.1).

Each adapter is a pure function over its corpus that emits typed entity
nodes + edge candidates. The graph_builder (separate module, C.1 BULK
step 7) merges entities across sources into the 3-level taxonomy
(``to:`` → ``meta:`` → ``ext:``) and persists rows to
``<bv>.capability_graph.{top_orders, meta_skills, extensions}``.

Per ``docs/23-databricks-capability-graph.md`` §23.1, the five sources
ranked by authority weight (§23.1.6):

  1. ``sdk_adapter``       authority 1.00  (Source 1, this dir)
  2. ``openapi_adapter``   authority 0.95  (Source 2, future C.1 BULK)
  3. ``docs_adapter``      authority 0.85  (Source 3, future C.1 BULK)
  4. ``labs_repo_adapter`` authority 0.75  (Source 5; Lakebridge first)
  5. ``blog_adapter``      authority 0.50  (Source 4; recency-decayed)

Hand-authored skills (``skills/<id>/SKILL.yaml``) ride at authority 1.00
through the ``exemplar_of`` field on extensions; they aren't a source
adapter — they're attached to extensions by the graph_builder.

Each adapter exposes:

  * Typed ``frozen, slots`` entity dataclasses (NOT row mirrors — those
    live in :mod:`brickvision_runtime.capability_graph.schemas.types`).
  * A pure top-level ``parse_*(...) -> *AdapterResult`` function that
    takes the corpus location + a ``parsed_at_ms`` clock and returns
    structured entities. Adapters never write to Delta themselves; the
    persist task (C.1 BULK step 8) takes care of that.
  * A ``*AdapterResult`` aggregate type with per-source metadata (e.g.,
    SDK version, source commit SHA) the indexer's ``corpus_snapshots``
    row records.
"""

from __future__ import annotations

from . import (
    blog_adapter,
    docs_adapter,
    labs_repo_adapter,
    openapi_adapter,
    sdk_adapter,
)

__all__ = [
    "blog_adapter",
    "docs_adapter",
    "labs_repo_adapter",
    "openapi_adapter",
    "sdk_adapter",
]
