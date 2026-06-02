"""Gold-set fixture loaders (Phase -1 N0-12.6 .. N0-12.14).

Each gold set is a Delta table in the deployed install (`<bv>.eval.*`); for
the spike harness we materialise small synthetic representatives in-process.
The full curation work (~3 engineer-weeks per gold set) is the Phase -1
content. These loaders define the *shape* and surface a tiny seed sample.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldKgSearchRow:
    """`<BV_CATALOG>.<BV_SCHEMA>.gold_kg_search_v1` row shape (N0-12.6)."""

    query_id: str
    query_class: str
    query_text: str
    expected_subject_ids: tuple[str, ...]
    curated_by: str
    curation_signature_hex: str


@dataclass(frozen=True)
class GoldKgWalkRow:
    """`<BV_CATALOG>.<BV_SCHEMA>.gold_kg_walk_v1` row shape (N0-12.7)."""

    walk_id: str
    seed_subject_id: str
    expected_neighbour_ids: tuple[str, ...]
    expected_max_depth: int


@dataclass(frozen=True)
class GoldMentionsRow:
    """`<BV_CATALOG>.<BV_SCHEMA>.gold_mentions_v1` row shape (N0-12.8)."""

    chunk_id: str
    chunk_text: str
    expected_mentions: tuple[tuple[str, str, str], ...]
    curated_by: str


@dataclass(frozen=True)
class GoldDocsLookupUrl:
    """`<BV_CATALOG>.<BV_SCHEMA>.gold_docs_lookup_urls_v1` row shape (N0-12.9)."""

    url_id: str
    url: str
    expected_freshness_signature_hex: str


@dataclass(frozen=True)
class WriteSideFixture:
    """`<BV_CATALOG>.<BV_SCHEMA>.write_side_fixture_v1` row shape (N0-12.13)."""

    fixture_id: str
    skill_id: str
    input_table_fqns: tuple[str, ...]
    expected_output_state: dict
    gold_metrics: dict


@dataclass(frozen=True)
class LakeflowDryRunCorpusRow:
    """`<BV_CATALOG>.<BV_SCHEMA>.lakeflow_dry_run_corpus_v1` row shape (N0-12.14)."""

    spec_id: str
    pipeline_spec_json: str
    expected_dry_run_outcome: str
    transform_class: str


SEED_GOLD_KG_SEARCH: tuple[GoldKgSearchRow, ...] = (
    GoldKgSearchRow(
        query_id="q-001",
        query_class="schema_lookup",
        query_text="payments silver tables",
        expected_subject_ids=("table:silver.payments.transactions",),
        curated_by="team:brickvision-substrate",
        curation_signature_hex="ed25519:placeholder",
    ),
)


__all__ = [
    "GoldDocsLookupUrl",
    "GoldKgSearchRow",
    "GoldKgWalkRow",
    "GoldMentionsRow",
    "LakeflowDryRunCorpusRow",
    "SEED_GOLD_KG_SEARCH",
    "ServingAliasDriftRow",
    "WriteSideFixture",
]
