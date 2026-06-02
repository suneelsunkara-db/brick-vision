"""BrickVision Databricks Capability Graph runtime (v0.7.7).

The capability graph is the 3-level taxonomy
``to:<top-order> > meta:<m> > ext:<e>`` produced by the multi-task
serverless Databricks Job ``bv_capability_indexer``. The Job crawls
five sources:

1. ``databricks-sdk-py`` Python AST (highest authority, 1.0)
2. Databricks REST OpenAPI specs (authority 0.9)
3. Public docs sites: docs.databricks.com (aws/azure/gcp) +
   learn.microsoft.com (authority 0.7)
4. databricks.com/blog (gap-fill for Data Modelling; authority 0.5)
5. ``github.com/databrickslabs/lakebridge`` (gap-fill for Migration &
   Ingestion; authority 0.6)

Each refresh produces a signed ``corpus_snapshots`` row; promotion is
atomic via the singleton ``active_snapshot_id`` row. Replay re-resolves
dual-substrate retrieval against the historical
``capability_graph_snapshot_id`` pin (the 6th replay pin added in
v0.7.7).

See ``docs/23-databricks-capability-graph.md`` for the complete design.

Submodule layout (C.1 SHELL — stubs in this commit; bulk authoring
follows in C.1 BULK across multiple sessions / engineers):

- ``schemas/``           — Delta DDL for the 13 ``<bv>.capability_graph.*`` tables
- ``sources/``           — 5 corpus adapters (SDK, OpenAPI, docs, blog, labs)
- ``extract.py``         — ``kg_extractor`` over the corpus
- ``embed.py``           — LLM_EMBEDDING_TASKS embeddings
- ``graph_builder.py``   — assembles 3-level taxonomy
- ``persist.py``         — Statement Execution INSERT/MERGE writes (Delta only)
- ``vs_upsert.py``       — Mosaic AI Vector Search direct upsert
- ``smoke.py``           — 5-query golden smoke test
- ``promote.py``         — atomic snapshot promotion
- ``retention.py``       — snapshot GC (30-day window)
- ``publish.py``         — Lakebase Autoscaling Synced-Tables publish (T14)
- ``retrieve.py``        — ``list_extensions_with_exemplars`` + ``kg_search_dual_substrate``
"""

from __future__ import annotations

# C.1 BULK step 1 (schemas) + step 2 (SDK source adapter) — installed;
# the indexer's persist task and the install pre-flight read ``schemas``;
# the indexer's ``extract_sdk`` task calls ``sources.sdk_adapter.parse_sdk``.
# Other submodules (remaining 4 source adapters under ``sources/``,
# ``extract``, ``embed``, ``graph_builder``, ``persist``, ``vs_upsert``,
# ``smoke``, ``promote``, ``retention``) follow in subsequent C.1 BULK steps.
from . import (
    embed,
    graph_builder,
    persist,
    promote,
    publish,
    retention,
    schemas,
    smoke,
    sources,
    vs_upsert,
)

__all__ = [
    "embed",
    "graph_builder",
    "persist",
    "promote",
    "publish",
    "retention",
    "schemas",
    "smoke",
    "sources",
    "vs_upsert",
]
