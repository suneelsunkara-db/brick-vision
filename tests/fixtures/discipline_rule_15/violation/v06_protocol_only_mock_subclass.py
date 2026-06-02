"""Violation 6 — public ``Protocol`` whose only concrete subclass is a
``Fake*``.

Triggers ``PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES`` (kind=protocol) AND
``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` (kind=class) for the
``FakeEmbeddingClient`` itself.

The seam check considers a Protocol "satisfied by mocks only" when
every concrete subclass of it within the scanned roots starts with
one of ``Fake``/``Mock``/``Stub``/``Dummy``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class EmbeddingClient(Protocol):
    """The interface the production module talks to."""

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...


class FakeEmbeddingClient:
    """The only concrete subclass — used by tests, never in production."""

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [[0.0] * 16 for _ in texts]
