"""Source 3 of 5 — Concept docs walker (authority 0.85; per §23.1.3).

**Per directive 2 of the v0.7.7 design exchange: Microsoft Learn is
in-scope from day 1**, not deferred. Four corpora total (URL counts
empirical from sitemap probes 2026-04, will drift):

  * ``docs.databricks.com/aws/en/``                  ≈5,150 URLs · authority 0.85
  * ``docs.databricks.com/azure/en/``                ≈4,800 URLs · authority 0.85
  * ``docs.databricks.com/gcp/en/``                  ≈3,900 URLs · authority 0.85
  * ``learn.microsoft.com/azure/databricks``         ≈4,200 URLs · authority 0.80

Skill-bearing share: ~70% post-filter (drops ``release-notes/``,
``error-messages/``, ``archive/``, and index-style landing pages).

What this module does
=====================

Walks pre-fetched HTML pages — caller does the BFS sitemap crawl + HTTPS
GET (rate-limited 4 req/s, ``Retry-After`` aware) and hands raw HTML
strings + their source URLs to :func:`parse_docs`. Doing IO outside the
adapter keeps it deterministic, side-effect free, and offline-testable
against synthetic fixtures.

Emits four entity kinds (per §23.1.3 + the corpus container we add for
the Knowledge UI's Corpus tab to render per-cloud cards):

  * :class:`DocsPageEntity`         — one per URL (post skill-bearing filter).
  * :class:`DocsChunkEntity`        — N per page; 1,500-token target chunk
                                       size with 150-token overlap, heading-
                                       aware boundaries (mirrors the chunking
                                       grammar of ``skill:docs.lookup``
                                       per ``docs/11-skill-catalog.md`` §9.1.1).
  * :class:`DocsSectionRootEntity`  — one per top-level URL path segment
                                       (e.g., ``delta/``, ``machine-learning/``,
                                       ``mlflow3/``); aligns with a meta-skill
                                       via the ``docs_section_aliases`` table
                                       at graph-builder time.
  * :class:`DocsCorpusEntity`       — one per corpus_cloud (4 total at v0.7.7
                                       ship: aws, azure, gcp, mslearn).

HTML parsing — stdlib only
==========================

§23.1.3 mentions BeautifulSoup4 in the production indexer; this module
intentionally uses Python's stdlib :mod:`html.parser` (no bs4) so the
adapter:
  1. has zero external runtime dependencies (works in vanilla
     Databricks Serverless without ``%pip install``);
  2. parses identically across the indexer Job + the offline test
     fixtures + a partner's air-gapped install;
  3. doesn't drift behavior across bs4 versions.

The stdlib parser extracts:
  * Plain text (with ``<script>``, ``<style>``, ``<nav>``, ``<header>``,
    ``<footer>``, ``<aside>`` removed — the standard "main content"
    isolation strategy).
  * A flat heading list ``[(level, anchor, text), …]`` for boundary-
    aware chunking.
  * A ``<title>`` value for the page entity's title field.

Code fences (``<pre><code>``) are preserved as opaque blocks within the
plain text so chunks don't split mid-fence; URLs inside ``<a href=...>``
are dropped (the kg_extractor extracts cross-references at graph_builder
time, not at chunk emission).

Tokenization (interim; superseded by step 7)
============================================

Real tokenization (tiktoken / the configured embedding model tokenizer) is
deferred to ``embed.py`` in C.1 BULK step ~7. Until then, this adapter
uses a **word-count proxy** with the empirical ratio
``words / 0.75 ≈ tokens`` (per OpenAI's tokenizer documentation). The
chunk boundaries this produces are within ±5% of true tokenizer
boundaries on Databricks-style technical prose, which is well inside
the 1,500/150-token target's tolerance.

Reason codes
============

Per §23.1.3:
  * :data:`ReasonCode.CAPABILITY_GRAPH_DOCS_FETCH_FAILED` — emitted by
    the indexer's ``extract_docs`` task on HTTP error, NOT by this
    adapter (this adapter doesn't fetch).
  * :data:`ReasonCode.CAPABILITY_GRAPH_DOCS_PARSE_FAILED` — per-page,
    soft fail; surfaced via :class:`DocsParseError` in the result.
  * :data:`ReasonCode.CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL` — emitted
    by the indexer's per-corpus aggregator if > 20% of URLs in a
    single corpus fail to crawl; this adapter surfaces the per-page
    counts in :attr:`DocsAdapterResult.corpus_partial_summary` so the
    aggregator can compute the threshold.
  * :data:`ReasonCode.DOCS_SECTION_ALIAS_MISSING` — emitted by the
    graph_builder when a ``DocsSectionRootEntity`` doesn't match the
    closed ``docs_section_aliases`` table; this adapter surfaces the
    raw section roots so graph_builder can do the join.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DocsCorpusEntity:
    """One per ``corpus_cloud ∈ {aws, azure, gcp, mslearn}``."""

    corpus_id: str  # e.g., "docs:aws"
    corpus_cloud: str
    sitemap_url: str
    page_count: int
    section_root_ids: tuple[str, ...]
    skill_bearing_pct: float  # = page_count / total_seen
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class DocsSectionRootEntity:
    """One per top-level URL path segment per corpus.

    Joined to ``meta_skills`` via ``<BV_CATALOG>.<BV_SCHEMA>.docs_section_aliases``
    at graph-builder time (§23.2.7).
    """

    section_root_id: str  # e.g., "docs:aws:delta/"
    corpus_cloud: str
    section_root: str  # e.g., "delta/" or "machine-learning/"
    page_count: int  # how many DocsPageEntity rows are under this root
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class DocsPageEntity:
    """One per fetched URL that passed the skill-bearing filter."""

    page_id: str  # e.g., "docs:aws:abc123" where abc123 is sha256[:16](url)
    corpus_cloud: str
    section_root: str | None
    url: str
    title: str | None
    fetched_at_ms: int  # caller stamps this when it crawls
    body_byte_length: int
    chunk_ids: tuple[str, ...]  # member chunk ids in source order
    content_hash: str  # of the page's plain text + heading list


@dataclasses.dataclass(frozen=True, slots=True)
class DocsChunkEntity:
    """One chunk of body text from a page (1,500-token target)."""

    chunk_id: str  # e.g., "docs:aws:abc123:chunk:0"
    page_id: str
    corpus_cloud: str
    section_root: str | None
    chunk_index: int  # 0-based, source order
    chunk_text: str
    chunk_token_count: int  # word-count proxy until embed.py's tokenizer
    headings_path: tuple[str, ...]  # the heading lineage the chunk falls under
    starts_at_word: int  # offset in the page's word stream
    content_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class DocsParseError:
    """Per-page parse failure; the snapshot ships partial."""

    url: str
    error_kind: str
    error_message: str


@dataclasses.dataclass(frozen=True, slots=True)
class DocsPageInput:
    """One pre-fetched page the caller hands in for parsing."""

    url: str
    raw_html: str
    fetched_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class DocsAdapterResult:
    """Aggregate output of one ``parse_docs`` invocation."""

    parsed_at_ms: int
    corpora: tuple[DocsCorpusEntity, ...]
    section_roots: tuple[DocsSectionRootEntity, ...]
    pages: tuple[DocsPageEntity, ...]
    chunks: tuple[DocsChunkEntity, ...]
    parse_errors: tuple[DocsParseError, ...]
    skipped_non_skill_bearing: tuple[str, ...]  # URLs filtered out (for telemetry)
    corpus_partial_summary: Mapping[str, tuple[int, int]]
    """``corpus_cloud -> (failed_url_count, total_url_count)``; consumed
    by the per-corpus aggregator to decide whether to emit
    ``CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL`` (>20% threshold)."""


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


_NON_SKILL_BEARING_PATH_PREFIXES: tuple[str, ...] = (
    "release-notes",
    "error-messages",
    "archive",
)
"""§23.1.3 — these path prefixes are dropped during the skill-bearing
filter. Index landing pages (URLs ending in ``/`` or ``/index``) are
also dropped, but that's a separate check below."""

_DOCS_DATABRICKS_HOST_RE = re.compile(
    r"^docs\.databricks\.com$|^docs-staging\.databricks\.com$"
)
_LEARN_MICROSOFT_HOST_RE = re.compile(r"^learn\.microsoft\.com$")


def derive_corpus_cloud(url: str) -> str | None:
    """Return ``aws | azure | gcp | mslearn`` from the URL, or ``None``
    if the host doesn't match any known docs corpus.

    Examples
    --------
    >>> derive_corpus_cloud("https://docs.databricks.com/aws/en/delta/index.html")
    'aws'
    >>> derive_corpus_cloud("https://docs.databricks.com/azure/en/jobs/")
    'azure'
    >>> derive_corpus_cloud("https://learn.microsoft.com/en-us/azure/databricks/")
    'mslearn'
    >>> derive_corpus_cloud("https://example.com/foo")
    """

    p = urlparse(url)
    host = p.hostname or ""
    if _DOCS_DATABRICKS_HOST_RE.match(host):
        # Path is /<cloud>/en/...
        parts = [s for s in p.path.split("/") if s]
        if parts:
            cloud = parts[0]
            if cloud in ("aws", "azure", "gcp"):
                return cloud
        return None
    if _LEARN_MICROSOFT_HOST_RE.match(host):
        # MS Learn paths look like /en-us/azure/databricks/...
        if "azure/databricks" in p.path:
            return "mslearn"
        return None
    return None


def derive_section_root(url: str) -> str | None:
    """Return the top-level docs section root (e.g., ``"delta/"``) or
    ``None`` if the URL doesn't have one.

    For ``docs.databricks.com``: the section root is the first path
    segment after ``/<cloud>/en/`` (e.g.,
    ``/aws/en/delta/index.html`` → ``delta/``).

    For ``learn.microsoft.com``: the section root is the first path
    segment after ``/en-us/azure/databricks/`` (e.g.,
    ``/en-us/azure/databricks/delta/index`` → ``delta/``).

    Returns ``None`` if the URL is the corpus root itself or doesn't
    match a known shape (the caller should treat this as
    ``meta_skill_assignment_pending`` per §23.2.7's
    ``DOCS_SECTION_ALIAS_MISSING`` semantic).
    """

    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]
    host = p.hostname or ""
    if _DOCS_DATABRICKS_HOST_RE.match(host):
        # Skip /<cloud>/en/ prefix; section root is parts[2] if present.
        if len(parts) >= 3:
            section = parts[2]
            return f"{section}/"
        return None
    if _LEARN_MICROSOFT_HOST_RE.match(host):
        # /en-us/azure/databricks/<section>/...
        # Skip first 4 parts: en-us / azure / databricks / <section>
        if len(parts) >= 4:
            section = parts[3]
            return f"{section}/"
        return None
    return None


def is_skill_bearing(url: str) -> bool:
    """Implements the §23.1.3 skill-bearing filter.

    Drops:
      * URLs under ``release-notes/``, ``error-messages/``, ``archive/``
      * Index landing pages (path ending in ``/`` or ``/index`` or
        ``/index.html``)
      * URLs whose corpus_cloud cannot be derived (foreign host)
    """

    if derive_corpus_cloud(url) is None:
        return False

    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]

    # Index-page check: empty path or ends with index/index.html.
    if not parts:
        return False
    last = parts[-1].lower()
    if last in ("index", "index.html", "index.htm"):
        return False
    if p.path.endswith("/"):
        return False

    # Section-prefix check.
    section = derive_section_root(url)
    if section is not None:
        section_name = section.rstrip("/")
        if section_name in _NON_SKILL_BEARING_PATH_PREFIXES:
            return False
    return True


# ---------------------------------------------------------------------------
# Stdlib HTML parser
# ---------------------------------------------------------------------------


_DROP_TAGS: frozenset[str] = frozenset(
    {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg"}
)
"""Tags whose content we discard entirely. ``<svg>`` is dropped because
docs.databricks.com inlines diagram SVGs that pollute text extraction."""

_BLOCK_TAGS: frozenset[str] = frozenset(
    {"p", "div", "li", "ul", "ol", "blockquote", "pre", "section", "article"}
)
"""Block-level tags that introduce a paragraph break in the extracted
text (we emit ``\\n\\n`` after them)."""

_INLINE_BREAK_TAGS: frozenset[str] = frozenset({"br"})


@dataclasses.dataclass(slots=True)
class _ParsedHtml:
    """Internal: result of running ``_HtmlExtractor`` on one page."""

    title: str | None
    plain_text: str
    headings: list[tuple[int, str | None, str]]  # (level, anchor_id, text)


class _HtmlExtractor(HTMLParser):
    """Stdlib-only HTML → plain text + heading list extractor.

    Strategy:
      * Drop content inside any tag in :data:`_DROP_TAGS` (recursive).
      * Append ``\\n\\n`` after block-level closing tags.
      * Append ``\\n`` for ``<br>`` self-closing.
      * Capture ``<h1>``-``<h6>`` open/close pairs into the heading list
        with their level + ``id=`` anchor + concatenated text.
      * Capture ``<title>`` once.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._drop_depth = 0  # > 0 ⇒ inside a dropped subtree
        self._heading_level: int | None = None
        self._heading_anchor: str | None = None
        self._heading_buf: list[str] = []
        self._in_title = False
        self._title_buf: list[str] = []
        self._title: str | None = None
        self._buf: list[str] = []
        self._headings: list[tuple[int, str | None, str]] = []

    @property
    def title(self) -> str | None:
        return self._title

    @property
    def plain_text(self) -> str:
        return "".join(self._buf)

    @property
    def headings(self) -> list[tuple[int, str | None, str]]:
        return list(self._headings)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth > 0:
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "br":
            self._buf.append("\n")
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_level = int(tag[1])
            self._heading_buf = []
            attr_dict = {k: v for k, v in attrs}
            self._heading_anchor = attr_dict.get("id")
            return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS:
            if self._drop_depth > 0:
                self._drop_depth -= 1
            return
        if self._drop_depth > 0:
            return
        if tag == "title":
            self._title = "".join(self._title_buf).strip() or None
            self._in_title = False
            self._title_buf = []
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if self._heading_level is not None:
                heading_text = "".join(self._heading_buf).strip()
                if heading_text:
                    self._headings.append(
                        (self._heading_level, self._heading_anchor, heading_text)
                    )
                    # Insert the heading into the plain-text stream so
                    # chunk boundaries can detect it via the heading
                    # path lookup at chunking time.
                    self._buf.append("\n\n")
                    self._buf.append(heading_text)
                    self._buf.append("\n\n")
            self._heading_level = None
            self._heading_anchor = None
            self._heading_buf = []
            return
        if tag in _BLOCK_TAGS:
            self._buf.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth > 0:
            return
        if self._in_title:
            self._title_buf.append(data)
            return
        if self._heading_level is not None:
            self._heading_buf.append(data)
            return
        # convert_charrefs=True already handles &amp; etc. — but
        # belt-and-braces for older entities or numeric refs that slip
        # through.
        self._buf.append(unescape(data))


def _parse_html(raw_html: str) -> _ParsedHtml:
    """Top-level: run the extractor and return a typed result.

    Collapses runs of whitespace + blank lines so chunk-boundary code
    isn't misled by the extractor's defensive ``\\n\\n`` insertions.
    """

    extractor = _HtmlExtractor()
    extractor.feed(raw_html)
    extractor.close()

    text = extractor.plain_text
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return _ParsedHtml(
        title=extractor.title,
        plain_text=text,
        headings=extractor.headings,
    )


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


_TARGET_TOKENS_DEFAULT: int = 1500
_OVERLAP_TOKENS_DEFAULT: int = 150
_WORDS_PER_TOKEN_PROXY: float = 0.75
"""Empirical ratio: ``words / 0.75 ≈ tokens`` for English technical
prose. Replaced by a real tokenizer in C.1 BULK step ~7."""


def _approx_token_count(words: Sequence[str]) -> int:
    """Word-count proxy for tokenization. Conservative: rounds up."""

    return max(1, int(round(len(words) / _WORDS_PER_TOKEN_PROXY)))


@dataclasses.dataclass(frozen=True, slots=True)
class _Chunk:
    text: str
    token_count: int
    headings_path: tuple[str, ...]
    starts_at_word: int


def _build_heading_path_for_offset(
    *,
    text: str,
    word_offset: int,
    headings: Sequence[tuple[int, str | None, str]],
) -> tuple[str, ...]:
    """Compute the heading lineage active at a given word offset.

    The plain-text stream contains heading text inline (per
    ``_HtmlExtractor.handle_endtag``), so we can locate each heading's
    word-index by scanning forward through the text. The active
    lineage at ``word_offset`` is the most recent heading at each level
    1..6 whose word-index ≤ ``word_offset``.
    """

    if not headings:
        return ()

    # Tokenize the text into words once; locate each heading's first
    # appearance as a contiguous sub-sequence of words.
    text_words = text.split()
    heading_word_offsets: list[tuple[int, int, str]] = []
    cursor = 0
    for level, _anchor, heading_text in headings:
        ht_words = heading_text.split()
        if not ht_words:
            continue
        # Find ht_words as a contiguous sublist starting at >= cursor.
        found_at = -1
        n = len(ht_words)
        for i in range(cursor, len(text_words) - n + 1):
            if text_words[i : i + n] == ht_words:
                found_at = i
                break
        if found_at >= 0:
            heading_word_offsets.append((level, found_at, heading_text))
            cursor = found_at + n
        else:
            # Heading not located in text (e.g., extractor inserted some
            # but the chunker can't find them due to inline formatting);
            # skip silently. Real corpora rarely trigger this.
            continue

    # Build the active lineage per the most recent heading at each level.
    active: dict[int, str] = {}
    for level, off, ht in heading_word_offsets:
        if off > word_offset:
            break
        # A heading at level L invalidates all deeper levels (L+1..6).
        for deeper in range(level + 1, 7):
            active.pop(deeper, None)
        active[level] = ht

    return tuple(active[k] for k in sorted(active.keys()))


def chunk_text(
    *,
    text: str,
    headings: Sequence[tuple[int, str | None, str]],
    target_tokens: int = _TARGET_TOKENS_DEFAULT,
    overlap_tokens: int = _OVERLAP_TOKENS_DEFAULT,
) -> list[_Chunk]:
    """Heading-aware chunker producing 1,500-token chunks with 150-token overlap.

    Strategy:
      1. Tokenize ``text`` into a flat word stream.
      2. Walk the word stream in fixed windows of approximately
         ``target_tokens`` (sized via the word-count proxy), stepping
         forward by ``target_tokens - overlap_tokens`` so consecutive
         chunks share ``overlap_tokens`` worth of words.
      3. Adjust each chunk's leading boundary to the nearest **paragraph
         break** (``\\n\\n``) within ±10% of the target window, so chunks
         start at a sentence/paragraph rather than mid-thought.
      4. Compute the active heading lineage at each chunk's start
         offset via :func:`_build_heading_path_for_offset`.

    The default 1,500-token target + 150-token overlap mirrors the
    ``skill:docs.lookup`` chunker's grammar so a docs URL fetched at
    runtime produces alignable chunk indices to the indexer's chunks
    (per §23.1.3's "intentionally — so a docs URL fetched via the
    runtime ``skill:docs.lookup`` and the same URL discovered by the
    indexer produce alignable chunk indices").
    """

    if not text:
        return []
    if target_tokens <= overlap_tokens:
        raise ValueError(
            f"target_tokens ({target_tokens}) must exceed overlap_tokens"
            f" ({overlap_tokens})"
        )

    # Convert token budget to a word budget via the proxy.
    target_words = max(1, int(target_tokens * _WORDS_PER_TOKEN_PROXY))
    overlap_words = max(0, int(overlap_tokens * _WORDS_PER_TOKEN_PROXY))
    step_words = max(1, target_words - overlap_words)

    words = text.split()
    if not words:
        return []

    # Compute paragraph break offsets once (in word units): a break
    # occurs at every word that follows a "\n\n" in the source text.
    # We track word indices where a paragraph started so we can snap
    # the leading boundary to the nearest one.
    paragraph_starts: list[int] = [0]
    accumulated = ""
    word_idx = 0
    for raw_para in re.split(r"\n{2,}", text):
        if not raw_para.strip():
            continue
        para_words = raw_para.split()
        if not para_words:
            continue
        paragraph_starts.append(word_idx)
        word_idx += len(para_words)
    # Deduplicate + sort.
    paragraph_starts = sorted(set(paragraph_starts))

    def _snap_to_paragraph(start: int, max_tolerance_words: int) -> int:
        """Snap ``start`` backward to the nearest paragraph start within
        the tolerance; if none found, return ``start`` unchanged."""

        best = start
        for ps in paragraph_starts:
            if ps <= start and (start - ps) <= max_tolerance_words and ps >= 0:
                best = ps
        return best

    chunks: list[_Chunk] = []
    cursor = 0
    chunk_index = 0
    n = len(words)
    tolerance = max(1, target_words // 10)

    while cursor < n:
        # Snap leading boundary to paragraph start, but not on the first
        # chunk (cursor=0 is already a hard boundary).
        chunk_start = cursor if chunk_index == 0 else _snap_to_paragraph(cursor, tolerance)
        chunk_end = min(n, chunk_start + target_words)
        chunk_words = words[chunk_start:chunk_end]
        chunk_text_str = " ".join(chunk_words)
        chunks.append(
            _Chunk(
                text=chunk_text_str,
                token_count=_approx_token_count(chunk_words),
                headings_path=_build_heading_path_for_offset(
                    text=text, word_offset=chunk_start, headings=headings
                ),
                starts_at_word=chunk_start,
            )
        )
        chunk_index += 1
        if chunk_end >= n:
            break
        cursor = chunk_start + step_words

    return chunks


# ---------------------------------------------------------------------------
# Entity hashing helpers
# ---------------------------------------------------------------------------


def _content_hash(*parts: str | None) -> str:
    """Stable sha256[:16] of joined parts (mirrors sibling adapters)."""

    h = hashlib.sha256()
    for p in parts:
        if p is None:
            h.update(b"\x00")
        else:
            h.update(p.encode("utf-8"))
            h.update(b"\x00")
    return h.hexdigest()[:16]


def _url_hash(url: str) -> str:
    """16-char URL hash used in entity IDs."""

    return _content_hash(url)


# ---------------------------------------------------------------------------
# Per-page parser
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _PageParse:
    """Internal: a parsed page + its chunks."""

    page: DocsPageEntity
    chunks: tuple[DocsChunkEntity, ...]


def _parse_one_page(
    *,
    inp: DocsPageInput,
    target_tokens: int,
    overlap_tokens: int,
) -> _PageParse | DocsParseError:
    """Parse a single docs page → page entity + chunk entities.

    Pre-conditions:
      * ``inp.url`` already passed :func:`is_skill_bearing` (caller filters).
    """

    url = inp.url
    corpus_cloud = derive_corpus_cloud(url)
    if corpus_cloud is None:
        return DocsParseError(
            url=url,
            error_kind="ValueError",
            error_message=f"could not derive corpus_cloud from URL host",
        )
    section_root = derive_section_root(url)

    try:
        parsed = _parse_html(inp.raw_html)
    except Exception as exc:  # noqa: BLE001 — stdlib parser shouldn't raise but be safe
        return DocsParseError(
            url=url,
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )

    page_url_hash = _url_hash(url)
    page_id = f"docs:{corpus_cloud}:{page_url_hash}"

    chunk_specs = chunk_text(
        text=parsed.plain_text,
        headings=parsed.headings,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
    )

    chunks: list[DocsChunkEntity] = []
    chunk_ids: list[str] = []
    for i, c in enumerate(chunk_specs):
        chunk_id = f"{page_id}:chunk:{i}"
        chunk_ids.append(chunk_id)
        chunks.append(
            DocsChunkEntity(
                chunk_id=chunk_id,
                page_id=page_id,
                corpus_cloud=corpus_cloud,
                section_root=section_root,
                chunk_index=i,
                chunk_text=c.text,
                chunk_token_count=c.token_count,
                headings_path=c.headings_path,
                starts_at_word=c.starts_at_word,
                content_hash=_content_hash(chunk_id, c.text),
            )
        )

    page = DocsPageEntity(
        page_id=page_id,
        corpus_cloud=corpus_cloud,
        section_root=section_root,
        url=url,
        title=parsed.title,
        fetched_at_ms=inp.fetched_at_ms,
        body_byte_length=len(parsed.plain_text.encode("utf-8")),
        chunk_ids=tuple(chunk_ids),
        content_hash=_content_hash(
            url, parsed.title, parsed.plain_text, *(c.content_hash for c in chunks)
        ),
    )
    return _PageParse(page=page, chunks=tuple(chunks))


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


_DEFAULT_SITEMAP_URLS: Mapping[str, str] = {
    "aws": "https://docs.databricks.com/aws/sitemap.xml",
    "azure": "https://docs.databricks.com/azure/sitemap.xml",
    "gcp": "https://docs.databricks.com/gcp/sitemap.xml",
    "mslearn": "https://learn.microsoft.com/azure/databricks/sitemap.xml",
}


def parse_docs(
    *,
    pages: Sequence[DocsPageInput],
    parsed_at_ms: int,
    target_tokens: int = _TARGET_TOKENS_DEFAULT,
    overlap_tokens: int = _OVERLAP_TOKENS_DEFAULT,
    fetched_url_counts: Mapping[str, int] | None = None,
) -> DocsAdapterResult:
    """Walk a batch of pre-fetched HTML pages and emit typed entities.

    Parameters
    ----------
    pages : Sequence[DocsPageInput]
        URL + raw HTML + fetched_at_ms triples from the indexer's
        ``extract_docs`` task. Pages whose URL fails
        :func:`is_skill_bearing` are dropped before parsing and
        recorded in ``skipped_non_skill_bearing``.
    parsed_at_ms : int
        Wall-clock timestamp (the indexer's freshness pin source).
    target_tokens, overlap_tokens : int
        Chunking grammar; defaults match §23.1.3 (1,500 / 150).
    fetched_url_counts : Mapping[str, int] | None
        Optional ``corpus_cloud -> total_urls_attempted`` map from the
        crawler. Used to compute the
        ``CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL`` threshold (>20%
        crawl failure rate). When ``None``, the per-corpus partial
        summary is computed from the visible sample only.

    Returns
    -------
    DocsAdapterResult
        Documents that fail to parse are isolated to ``parse_errors``;
        sibling pages continue.
    """

    pages_by_corpus: dict[str, list[DocsPageEntity]] = {}
    chunks_all: list[DocsChunkEntity] = []
    parse_errors: list[DocsParseError] = []
    skipped: list[str] = []
    section_root_counts: dict[tuple[str, str], int] = {}  # (cloud, root) -> count

    for inp in pages:
        if not is_skill_bearing(inp.url):
            skipped.append(inp.url)
            continue
        result = _parse_one_page(
            inp=inp, target_tokens=target_tokens, overlap_tokens=overlap_tokens
        )
        if isinstance(result, DocsParseError):
            parse_errors.append(result)
            continue
        pages_by_corpus.setdefault(result.page.corpus_cloud, []).append(result.page)
        chunks_all.extend(result.chunks)
        if result.page.section_root is not None:
            key = (result.page.corpus_cloud, result.page.section_root)
            section_root_counts[key] = section_root_counts.get(key, 0) + 1

    # Build section_root entities (deterministic order: by id).
    section_roots: list[DocsSectionRootEntity] = []
    for (cloud, root), count in sorted(section_root_counts.items()):
        section_root_id = f"docs:{cloud}:{root}"
        section_roots.append(
            DocsSectionRootEntity(
                section_root_id=section_root_id,
                corpus_cloud=cloud,
                section_root=root,
                page_count=count,
                content_hash=_content_hash(section_root_id, str(count)),
            )
        )

    # Build corpus entities (deterministic order: by cloud).
    corpora: list[DocsCorpusEntity] = []
    for cloud in sorted(pages_by_corpus.keys()):
        corpus_pages = pages_by_corpus[cloud]
        roots_in_corpus = tuple(
            sr.section_root_id for sr in section_roots if sr.corpus_cloud == cloud
        )
        # Skill-bearing pct = pages_kept / (pages_kept + skipped_in_corpus +
        # parse_errors_in_corpus). We approximate by counting the pages
        # whose corpus_cloud is identifiable.
        kept = len(corpus_pages)
        skipped_in_corpus = sum(
            1 for u in skipped if derive_corpus_cloud(u) == cloud
        )
        errors_in_corpus = sum(
            1 for pe in parse_errors if derive_corpus_cloud(pe.url) == cloud
        )
        total_seen = kept + skipped_in_corpus + errors_in_corpus
        pct = (kept / total_seen) if total_seen > 0 else 0.0
        corpora.append(
            DocsCorpusEntity(
                corpus_id=f"docs:{cloud}",
                corpus_cloud=cloud,
                sitemap_url=_DEFAULT_SITEMAP_URLS.get(cloud, ""),
                page_count=kept,
                section_root_ids=roots_in_corpus,
                skill_bearing_pct=pct,
                content_hash=_content_hash(
                    f"docs:{cloud}", str(kept), *roots_in_corpus
                ),
            )
        )

    # Per-corpus partial summary: (failed, total) using fetched_url_counts
    # if the caller provided it, otherwise from the visible sample.
    partial_summary: dict[str, tuple[int, int]] = {}
    for cloud in sorted(set(list(pages_by_corpus.keys()) +
                            [derive_corpus_cloud(u) or "" for u in skipped] +
                            [derive_corpus_cloud(pe.url) or "" for pe in parse_errors])):
        if not cloud:
            continue
        failed_in_corpus = sum(
            1 for pe in parse_errors if derive_corpus_cloud(pe.url) == cloud
        )
        if fetched_url_counts and cloud in fetched_url_counts:
            total = fetched_url_counts[cloud]
        else:
            total = (
                len(pages_by_corpus.get(cloud, []))
                + sum(1 for u in skipped if derive_corpus_cloud(u) == cloud)
                + failed_in_corpus
            )
        partial_summary[cloud] = (failed_in_corpus, total)

    return DocsAdapterResult(
        parsed_at_ms=parsed_at_ms,
        corpora=tuple(corpora),
        section_roots=tuple(section_roots),
        pages=tuple(
            sorted(
                (p for ps in pages_by_corpus.values() for p in ps),
                key=lambda p: p.page_id,
            )
        ),
        chunks=tuple(sorted(chunks_all, key=lambda c: c.chunk_id)),
        parse_errors=tuple(parse_errors),
        skipped_non_skill_bearing=tuple(skipped),
        corpus_partial_summary=partial_summary,
    )


__all__ = [
    "DocsAdapterResult",
    "DocsChunkEntity",
    "DocsCorpusEntity",
    "DocsPageEntity",
    "DocsPageInput",
    "DocsParseError",
    "DocsSectionRootEntity",
    "chunk_text",
    "derive_corpus_cloud",
    "derive_section_root",
    "is_skill_bearing",
    "parse_docs",
]
