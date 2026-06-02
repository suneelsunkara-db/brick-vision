"""Violation 1 — class with the ``Fake`` prefix.

Triggers ``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` (kind=class).
"""

from __future__ import annotations


class FakeFMSClient:
    """Returns deterministic embeddings without calling the real FMS."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]
