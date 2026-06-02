"""Clean 1 — production class names a real dependency, not a mock.

Demonstrates the canonical clean pattern: the wrapper class wraps the
real third-party library directly. Test-only behavior is gated through
``BV_FAKE_LLM`` *inside* the production code path (not via a Protocol
seam).
"""

from __future__ import annotations

import os
from collections.abc import Sequence


class FoundationModelEmbeddingClient:
    """Wraps Mosaic AI Foundation Model Serving for embeddings.

    Calls ``WorkspaceClient.serving_endpoints.query()`` directly. No
    ``embed_batch``-Protocol seam in the way; tests monkeypatch this
    class's ``_invoke()`` method or set ``BV_FAKE_LLM=true`` to route
    through the fixture loader inside ``_invoke``.
    """

    def __init__(self, endpoint_name: str = "databricks-gte-large-en") -> None:
        self.endpoint_name = endpoint_name

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._invoke(text) for text in texts]

    def _invoke(self, text: str) -> list[float]:
        if os.environ.get("BV_FAKE_LLM", "false").lower() == "true":
            return self._load_fixture(text)
        return self._call_workspace_client(text)

    def _call_workspace_client(self, text: str) -> list[float]:
        raise NotImplementedError(
            "wired to databricks.sdk.WorkspaceClient.serving_endpoints.query"
            " in the real production module"
        )

    def _load_fixture(self, text: str) -> list[float]:
        raise NotImplementedError(
            "wired to tests/fixtures/capability_graph/canned_embeddings.json"
            " in the real production module"
        )
