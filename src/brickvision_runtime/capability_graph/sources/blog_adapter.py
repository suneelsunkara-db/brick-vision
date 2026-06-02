"""Source 4 of 5 — Databricks engineering blog walker (authority 0.50;
recency-decayed; per §23.1.5).

What this module does
=====================

Walks pre-fetched blog post HTML — caller does the BFS sitemap crawl
of ``https://www.databricks.com/blog/sitemap.xml`` (rate-limited
4 req/s, ``Retry-After`` aware) and hands raw HTML strings + their
source URLs to :func:`parse_blog`. Doing IO outside the adapter keeps
it deterministic, side-effect free, and offline-testable.

Why a SEPARATE adapter from docs_adapter
========================================

Although blog posts and concept docs both come from
``databricks.com``-family hosts, they differ in three meaningful ways
that warrant a separate adapter:

  1. **Single global corpus** — no per-cloud variance (unlike docs's
     aws/azure/gcp/mslearn fan-out). The blog has one host
     (``www.databricks.com/blog``) and one effective worldview.
  2. **Authority is recency-decayed** — base authority 0.50 with a
     365-day half-life (per §23.1.5). graph_builder must scale
     individual post authority by ``0.5 ** (days_since_publish / 365)``.
     This is the only source in the corpus with recency-aware
     authority; the SDK/OpenAPI/docs/labs sources are all "current
     state of truth" and don't decay.
  3. **Cheap pre-filter is a denylist over allowlist** — the blog
     surface is mostly skill-bearing, with a small number of obvious
     non-skill categories (customer-stories, events, news,
     company-blog) that we filter cheaply via path-prefix. Real
     skill-bearing scoring happens at graph_builder time via the
     LLM-bound ``kg_extractor`` symbolic role.

Reused from docs_adapter
========================

This adapter intentionally **shares the chunking grammar with
docs_adapter** by importing its helpers:

  * :func:`docs_adapter.chunk_text` — heading-aware 1,500-token chunks
    with 150-token overlap. Identical grammar so a docs_chunk and a
    blog_chunk are interchangeable at vector-search retrieval time.
  * ``docs_adapter._parse_html`` — stdlib HTML→text extractor with
    drop-tag scrubbing (``<script>/<style>/<nav>/...``).
  * ``docs_adapter._content_hash``, ``docs_adapter._url_hash`` — stable
    sha256[:16] hashing helpers for entity IDs.

Cross-package private imports (the leading-underscore reach-arounds)
are intentional here: blog_adapter and docs_adapter are siblings in
``brickvision_runtime.capability_graph.sources``, and the chunking
grammar contract is package-internal — see §23.1.5 "the chunking
grammar matches docs_adapter exactly so retrieval results compose
across substrates." Refactoring these to a shared
``_html_chunking.py`` module is a v0.7.8 task, not blocking here.

Reason codes
============

Per §23.1.5:
  * :data:`ReasonCode.CAPABILITY_GRAPH_BLOG_FETCH_FAILED` — emitted by
    the indexer's ``extract_blog`` task on HTTP error, NOT by this
    adapter (this adapter doesn't fetch).
  * :data:`ReasonCode.CAPABILITY_GRAPH_BLOG_PARSE_FAILED` — per-post,
    soft fail; surfaced via :class:`BlogParseError` in the result.
  * :data:`ReasonCode.BLOG_META_SKILL_INFERENCE_FAILED` — emitted by
    graph_builder when the LLM mention extractor can't link a blog
    post to any meta-skill with a confidence ≥0.7. NOT this adapter's
    concern — we surface raw entities, graph_builder does the linkage.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

from .docs_adapter import (
    chunk_text,
    _content_hash,
    _parse_html,
    _url_hash,
    _TARGET_TOKENS_DEFAULT,
    _OVERLAP_TOKENS_DEFAULT,
)


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BlogCorpusEntity:
    """One global corpus container per indexer run."""

    corpus_id: str  # e.g., "blog:databricks"
    sitemap_url: str
    post_count: int
    earliest_post_ms: int | None
    latest_post_ms: int | None
    skill_bearing_pct: float  # = post_count / total_seen
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class BlogPostEntity:
    """One per fetched URL that passed the skill-bearing filter."""

    post_id: str  # e.g., "blog:abc123" where abc123 = sha256[:16](url)
    url: str
    title: str | None
    published_at_ms: int | None  # epoch ms; None when meta tag absent
    authors: tuple[str, ...]  # may be empty
    fetched_at_ms: int  # caller stamps when crawled
    body_byte_length: int
    chunk_ids: tuple[str, ...]  # member chunk ids in source order
    base_authority: float  # 0.50 — the un-decayed source authority
    content_hash: str  # of url + title + body + chunk hashes


@dataclasses.dataclass(frozen=True, slots=True)
class BlogChunkEntity:
    """One chunk of body text from a post."""

    chunk_id: str  # e.g., "blog:abc123:chunk:0"
    post_id: str
    chunk_index: int  # 0-based, source order
    chunk_text: str
    chunk_token_count: int  # word-count proxy until embed.py's tokenizer
    headings_path: tuple[str, ...]  # heading lineage at chunk start
    starts_at_word: int
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class BlogParseError:
    """Per-post parse failure; the snapshot ships partial."""

    url: str
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class BlogPostInput:
    """One pre-fetched blog post the caller hands in for parsing."""

    url: str
    raw_html: str
    fetched_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class BlogAdapterResult:
    """Aggregate output of one ``parse_blog`` invocation."""

    parsed_at_ms: int
    corpus: BlogCorpusEntity | None  # None when zero posts pass filter
    posts: tuple[BlogPostEntity, ...]
    chunks: tuple[BlogChunkEntity, ...]
    parse_errors: tuple[BlogParseError, ...]
    skipped_non_skill_bearing: tuple[str, ...]  # filtered URLs (telemetry)


# ---------------------------------------------------------------------------
# URL helpers + skill-bearing filter
# ---------------------------------------------------------------------------


_BLOG_HOST_RE = re.compile(r"^(www\.)?databricks\.com$|^databricks\.com$")
"""Production blog host. Staging hosts (``staging.databricks.com``)
are intentionally NOT matched — the indexer should only ingest the
production blog corpus."""


_NON_SKILL_BEARING_BLOG_PATH_PREFIXES: tuple[str, ...] = (
    "blog/category/customer-stories",
    "blog/category/events",
    "blog/category/news",
    "blog/category/company-blog",
    "blog/category/announcements",
    "blog/category/partners",
)
"""§23.1.5 — these path prefixes are the cheap pre-filter denylist.
Real skill-bearing scoring (whether a kept post actually contains
skill-relevant technical content) is graph_builder's responsibility
via the LLM-bound ``kg_extractor`` role; this filter just removes the
obviously-non-technical categories."""


def is_databricks_blog_url(url: str) -> bool:
    """Return True iff the URL is on ``databricks.com/blog``."""

    p = urlparse(url)
    host = (p.hostname or "").lower()
    if not _BLOG_HOST_RE.match(host):
        return False
    parts = [s for s in p.path.split("/") if s]
    return len(parts) >= 1 and parts[0] == "blog"


def is_skill_bearing(url: str) -> bool:
    """Implements the §23.1.5 cheap pre-filter denylist.

    Drops:
      * Foreign hosts (anything not on ``databricks.com``).
      * Blog category pages themselves (e.g., ``/blog/category/engineering``)
        because they're index-style landing pages with no body content.
      * Posts under any prefix in :data:`_NON_SKILL_BEARING_BLOG_PATH_PREFIXES`.
      * Sitemap, RSS, and feed URLs.
      * The blog homepage (path is just ``/blog`` or ``/blog/``).
    """

    if not is_databricks_blog_url(url):
        return False

    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]

    # Blog homepage or empty post slug.
    if len(parts) <= 1:
        return False

    # Sitemap / RSS / feeds.
    last = parts[-1].lower()
    if last in ("sitemap.xml", "rss.xml", "feed.xml", "atom.xml", "feed", "rss"):
        return False

    # Category-landing pages: e.g., /blog/category/engineering with
    # nothing after. Length is exactly 3: ['blog', 'category', '<cat>'].
    if len(parts) == 3 and parts[1] == "category":
        return False

    # Path prefix denylist.
    path_no_leading_slash = p.path.lstrip("/")
    for prefix in _NON_SKILL_BEARING_BLOG_PATH_PREFIXES:
        if path_no_leading_slash.startswith(prefix + "/") or path_no_leading_slash == prefix:
            return False

    return True


# ---------------------------------------------------------------------------
# Meta-tag extraction (regex-based; stdlib parser would require subclassing)
# ---------------------------------------------------------------------------


_META_PUBLISHED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r'<meta\s+[^>]*property\s*=\s*"article:published_time"\s+[^>]*content\s*=\s*"([^"]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta\s+[^>]*content\s*=\s*"([^"]+)"\s+[^>]*property\s*=\s*"article:published_time"',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta\s+[^>]*name\s*=\s*"article:published_time"\s+[^>]*content\s*=\s*"([^"]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'<time[^>]*datetime\s*=\s*"([^"]+)"',
        re.IGNORECASE,
    ),
)


_META_AUTHOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r'<meta\s+[^>]*property\s*=\s*"article:author"\s+[^>]*content\s*=\s*"([^"]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta\s+[^>]*content\s*=\s*"([^"]+)"\s+[^>]*property\s*=\s*"article:author"',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta\s+[^>]*name\s*=\s*"author"\s+[^>]*content\s*=\s*"([^"]+)"',
        re.IGNORECASE,
    ),
)


_ISO_8601_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


def _parse_iso_8601_to_ms(s: str) -> int | None:
    """Parse an ISO 8601 date/datetime string to epoch milliseconds (UTC).

    We avoid :func:`datetime.fromisoformat` because Python <3.11 can't
    handle the trailing ``Z`` and various tz formats; this regex
    handles the subset of ISO formats blog meta tags actually emit
    (date-only, datetime+Z, datetime+offset).
    """

    m = _ISO_8601_RE.match(s.strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = int(m.group(4)) if m.group(4) else 0
    minute = int(m.group(5)) if m.group(5) else 0
    second = int(m.group(6)) if m.group(6) else 0

    # Compute epoch ms via calendar rules (UTC). We don't bring in
    # datetime to keep this module dependency-free at the helper
    # level; days-since-epoch is a closed-form Zeller-like
    # computation but Python's datetime is fine to use here for
    # readability — the parser stays regex-driven, only the date
    # math uses stdlib.
    import datetime as _dt

    try:
        dt = _dt.datetime(year, month, day, hour, minute, second, tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    epoch = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    return int((dt - epoch).total_seconds() * 1000)


def _extract_published_at_ms(raw_html: str) -> int | None:
    """Return the post's publish timestamp in epoch ms, or None."""

    for pat in _META_PUBLISHED_PATTERNS:
        m = pat.search(raw_html)
        if m:
            ts = _parse_iso_8601_to_ms(m.group(1))
            if ts is not None:
                return ts
    return None


def _extract_authors(raw_html: str) -> tuple[str, ...]:
    """Return tuple of author names (may be empty).

    The blog's article tags often expose multiple ``article:author``
    metas — one per author — so we collect all matches across every
    pattern variant and dedupe while preserving first-seen order.
    """

    seen: list[str] = []
    seen_set: set[str] = set()
    for pat in _META_AUTHOR_PATTERNS:
        for m in pat.finditer(raw_html):
            name = m.group(1).strip()
            if name and name not in seen_set:
                seen.append(name)
                seen_set.add(name)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Recency-decay utility (graph_builder is the actual caller; we expose it
# here so the formula has exactly one home)
# ---------------------------------------------------------------------------


_BLOG_BASE_AUTHORITY: float = 0.50
_BLOG_HALF_LIFE_DAYS: float = 365.0


def compute_recency_decayed_authority(
    *,
    base_authority: float = _BLOG_BASE_AUTHORITY,
    published_at_ms: int | None,
    now_ms: int,
    half_life_days: float = _BLOG_HALF_LIFE_DAYS,
) -> float:
    """Return ``base_authority * 0.5 ** (days_old / half_life_days)``.

    Per §23.1.5: blog authority is the only recency-decayed authority
    in the corpus. A 1-year-old post → 0.25; a 2-year-old → 0.125.

    Parameters
    ----------
    base_authority : float
        The un-decayed source authority. Defaults to 0.50 (blog).
    published_at_ms : int | None
        Post publish timestamp in epoch ms. When ``None`` (no meta
        tag), we treat the post as one half-life old (i.e., decay
        factor 0.5) rather than dropping it; graph_builder may still
        link it to a meta-skill, just at reduced authority.
    now_ms : int
        Current wall-clock time in epoch ms. The indexer pins this so
        decay is reproducible.
    half_life_days : float
        Decay half-life. Defaults to 365 (per spec).
    """

    if published_at_ms is None:
        return base_authority * 0.5
    days_old_ms = max(0, now_ms - published_at_ms)
    days_old = days_old_ms / (1000.0 * 60.0 * 60.0 * 24.0)
    return base_authority * (0.5 ** (days_old / half_life_days))


# ---------------------------------------------------------------------------
# Per-post parser
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _PostParse:
    """Internal: a parsed post + its chunks."""

    post: BlogPostEntity
    chunks: tuple[BlogChunkEntity, ...]


def _parse_one_post(
    *,
    inp: BlogPostInput,
    target_tokens: int,
    overlap_tokens: int,
) -> _PostParse | BlogParseError:
    """Parse a single blog post → post entity + chunk entities.

    Pre-conditions:
      * ``inp.url`` already passed :func:`is_skill_bearing` (caller filters).
    """

    url = inp.url
    try:
        parsed = _parse_html(inp.raw_html)
    except Exception as exc:  # noqa: BLE001 — defensive
        return BlogParseError(
            url=url,
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )

    published_at_ms = _extract_published_at_ms(inp.raw_html)
    authors = _extract_authors(inp.raw_html)

    post_url_hash = _url_hash(url)
    post_id = f"blog:{post_url_hash}"

    chunk_specs = chunk_text(
        text=parsed.plain_text,
        headings=parsed.headings,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
    )

    chunks: list[BlogChunkEntity] = []
    chunk_ids: list[str] = []
    for i, c in enumerate(chunk_specs):
        chunk_id = f"{post_id}:chunk:{i}"
        chunk_ids.append(chunk_id)
        chunks.append(
            BlogChunkEntity(
                chunk_id=chunk_id,
                post_id=post_id,
                chunk_index=i,
                chunk_text=c.text,
                chunk_token_count=c.token_count,
                headings_path=c.headings_path,
                starts_at_word=c.starts_at_word,
                content_hash=_content_hash(chunk_id, c.text),
            )
        )

    post = BlogPostEntity(
        post_id=post_id,
        url=url,
        title=parsed.title,
        published_at_ms=published_at_ms,
        authors=authors,
        fetched_at_ms=inp.fetched_at_ms,
        body_byte_length=len(parsed.plain_text.encode("utf-8")),
        chunk_ids=tuple(chunk_ids),
        base_authority=_BLOG_BASE_AUTHORITY,
        content_hash=_content_hash(
            url,
            parsed.title,
            parsed.plain_text,
            *(c.content_hash for c in chunks),
        ),
    )
    return _PostParse(post=post, chunks=tuple(chunks))


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


_BLOG_SITEMAP_URL: str = "https://www.databricks.com/blog/sitemap.xml"


def parse_blog(
    *,
    posts: Sequence[BlogPostInput],
    parsed_at_ms: int,
    target_tokens: int = _TARGET_TOKENS_DEFAULT,
    overlap_tokens: int = _OVERLAP_TOKENS_DEFAULT,
) -> BlogAdapterResult:
    """Walk a batch of pre-fetched blog post HTML pages and emit typed entities.

    Parameters
    ----------
    posts : Sequence[BlogPostInput]
        URL + raw HTML + fetched_at_ms triples from the indexer's
        ``extract_blog`` task. Posts whose URL fails
        :func:`is_skill_bearing` are dropped before parsing and
        recorded in ``skipped_non_skill_bearing``.
    parsed_at_ms : int
        Wall-clock timestamp (the indexer's freshness pin source).
    target_tokens, overlap_tokens : int
        Chunking grammar; defaults match docs_adapter (1,500 / 150) so
        retrieval results compose across substrates.

    Returns
    -------
    BlogAdapterResult
        Posts that fail to parse are isolated to ``parse_errors``;
        sibling posts continue. When zero posts pass the skill-bearing
        filter, ``corpus`` is ``None`` and the indexer's
        ``promote_corpus`` task should treat the blog substrate as
        empty for this snapshot (not a failure — the prior snapshot's
        blog rows remain referenceable until retention drops them).
    """

    parsed_posts: list[BlogPostEntity] = []
    parsed_chunks: list[BlogChunkEntity] = []
    parse_errors: list[BlogParseError] = []
    skipped: list[str] = []

    for inp in posts:
        if not is_skill_bearing(inp.url):
            skipped.append(inp.url)
            continue
        result = _parse_one_post(
            inp=inp, target_tokens=target_tokens, overlap_tokens=overlap_tokens
        )
        if isinstance(result, BlogParseError):
            parse_errors.append(result)
            continue
        parsed_posts.append(result.post)
        parsed_chunks.extend(result.chunks)

    parsed_posts.sort(key=lambda p: p.post_id)
    parsed_chunks.sort(key=lambda c: c.chunk_id)

    # Build the corpus entity (None if zero kept).
    corpus: BlogCorpusEntity | None = None
    if parsed_posts:
        published_timestamps = [
            p.published_at_ms for p in parsed_posts if p.published_at_ms is not None
        ]
        earliest = min(published_timestamps) if published_timestamps else None
        latest = max(published_timestamps) if published_timestamps else None

        kept = len(parsed_posts)
        skipped_count = len(skipped)
        error_count = len(parse_errors)
        total_seen = kept + skipped_count + error_count
        pct = (kept / total_seen) if total_seen > 0 else 0.0

        corpus = BlogCorpusEntity(
            corpus_id="blog:databricks",
            sitemap_url=_BLOG_SITEMAP_URL,
            post_count=kept,
            earliest_post_ms=earliest,
            latest_post_ms=latest,
            skill_bearing_pct=pct,
            content_hash=_content_hash(
                "blog:databricks",
                str(kept),
                *(p.content_hash for p in parsed_posts),
            ),
        )

    return BlogAdapterResult(
        parsed_at_ms=parsed_at_ms,
        corpus=corpus,
        posts=tuple(parsed_posts),
        chunks=tuple(parsed_chunks),
        parse_errors=tuple(parse_errors),
        skipped_non_skill_bearing=tuple(skipped),
    )


__all__ = [
    "BlogAdapterResult",
    "BlogChunkEntity",
    "BlogCorpusEntity",
    "BlogParseError",
    "BlogPostEntity",
    "BlogPostInput",
    "compute_recency_decayed_authority",
    "is_databricks_blog_url",
    "is_skill_bearing",
    "parse_blog",
]
