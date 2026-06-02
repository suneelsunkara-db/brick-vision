"""Deterministic chunker for ``skill:docs.lookup``.

The chunker is intentionally stand-alone (no model calls) so the
``DocsLookupIdempotence`` scorer can re-run the same input and assert
byte-identical output. Token counting uses whitespace splits — the
real production driver swaps in the embedding tokenizer at install
time, but the *boundaries* must be chosen by a stateless function so
replay can reproduce them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import urllib.request
from collections.abc import Iterable
from typing import Any


@dataclasses.dataclass(frozen=True, slots=True)
class Chunk:
    document_id: str
    chunk_id: str
    chunk_index: int
    chunk_text: str
    chunk_hash: str
    start_token: int
    end_token: int


def hash_url_to_document_id(url: str) -> str:
    """Stable document id derived from the URL."""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"doc:{digest}"


def chunk_document(
    *,
    document_id: str,
    text: str,
    chunk_size_tokens: int = 800,
    chunk_overlap_tokens: int = 80,
) -> list[Chunk]:
    """Chunk ``text`` into fixed token windows with overlap.

    The token splitter is whitespace-only — the only contract that
    matters is **determinism**. The driver embeds with whatever
    tokenizer ``embedding_default`` resolves to.
    """

    if chunk_size_tokens <= 0:
        raise ValueError("chunk_size_tokens must be positive")
    if chunk_overlap_tokens < 0 or chunk_overlap_tokens >= chunk_size_tokens:
        raise ValueError("chunk_overlap_tokens must be in [0, chunk_size_tokens)")

    tokens = text.split()
    if not tokens:
        return []

    chunks: list[Chunk] = []
    stride = chunk_size_tokens - chunk_overlap_tokens
    idx = 0
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size_tokens, len(tokens))
        window = tokens[start:end]
        chunk_text = " ".join(window)
        chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        chunks.append(
            Chunk(
                document_id=document_id,
                chunk_id=f"{document_id}:chunk:{idx:04d}",
                chunk_index=idx,
                chunk_text=chunk_text,
                chunk_hash=chunk_hash,
                start_token=start,
                end_token=end,
            )
        )
        if end == len(tokens):
            break
        idx += 1
        start += stride

    return chunks


def build_content_claim_values(chunks: Iterable[Chunk]) -> list[dict[str, Any]]:
    """Build the ``value`` payloads for the CONTENT Claim emit step."""

    out: list[dict[str, Any]] = []
    for c in chunks:
        out.append(
            {
                "chunk_text": c.chunk_text,
                "metadata": {
                    "document_id": c.document_id,
                    "chunk_id": c.chunk_id,
                    "chunk_index": c.chunk_index,
                    "chunk_hash": c.chunk_hash,
                    "start_token": c.start_token,
                    "end_token": c.end_token,
                },
            }
        )
    return out


def fetch_url(url: str, *, timeout_seconds: int = 30) -> str:
    """Fetch a documentation page for ``tool:docs.fetch_url``."""

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BrickVision-docs-lookup/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


__all__ = [
    "Chunk",
    "build_content_claim_values",
    "chunk_document",
    "fetch_url",
    "hash_url_to_document_id",
]
