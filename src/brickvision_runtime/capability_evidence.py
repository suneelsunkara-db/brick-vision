"""Utilities for validating capability evidence passed into skills.

Hand-authored skills describe execution contracts. They may appear in audit
provenance, but they are not source-grounded capability evidence for API use.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

SOURCE_GROUNDING_KINDS = frozenset({"sdk", "openapi", "docs", "labs", "blog"})
CONTRACT_ONLY_KINDS = frozenset({"hand_authored"})
CAPABILITY_REF_PREFIXES = (
    "ext:",
    "meta:",
    "sdk:",
    "openapi:",
    "docs:",
    "doc:",
)


def is_capability_ref(ref: str) -> bool:
    """Return true for BrickVision capability refs and grounded source refs."""

    return ref.startswith(CAPABILITY_REF_PREFIXES)


def capability_ref(item: Mapping[str, Any]) -> str:
    """Extract the most specific capability reference from an evidence item."""

    return str(
        item.get("entity_id")
        or item.get("operation_id")
        or item.get("method_id")
        or item.get("ref")
        or ""
    ).strip()


def source_kinds(item: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract normalized source kinds from a capability evidence item."""

    kinds: set[str] = set()
    for key in ("source_kind", "source_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            kinds.add(value.strip())

    value = item.get("source_kinds")
    if isinstance(value, str) and value.strip():
        kinds.update(part.strip() for part in value.split(",") if part.strip())
    elif isinstance(value, Iterable):
        for part in value:
            if isinstance(part, str) and part.strip():
                kinds.add(part.strip())

    for chunk in item.get("contributing_chunks") or ():
        if isinstance(chunk, Mapping):
            kinds.update(source_kinds(chunk))

    return tuple(sorted(kinds))


def is_source_grounded_capability_evidence(item: Mapping[str, Any]) -> bool:
    """Return true when an item is grounded in indexed source artifacts."""

    ref = capability_ref(item)
    if not is_capability_ref(ref):
        return False

    kinds = set(source_kinds(item))
    if kinds & SOURCE_GROUNDING_KINDS:
        return True
    if kinds and kinds <= CONTRACT_ONLY_KINDS:
        return False

    # Some callers pass raw source refs without an explicit source_kind.
    # Treat these as grounded because the ref itself names the indexed layer.
    return ref.startswith(("sdk:", "openapi:", "docs:", "doc:"))


def source_grounded_capability_refs(
    items: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return de-duplicated source-grounded capability refs."""

    refs: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not is_source_grounded_capability_evidence(item):
            nested = item.get("capability_refs")
            if not isinstance(nested, list):
                continue
            candidates = tuple(str(value).strip() for value in nested)
        else:
            candidates = (capability_ref(item),)
        for ref in candidates:
            if is_capability_ref(ref) and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    return refs


def has_contract_only_capability_evidence(
    items: Iterable[Mapping[str, Any]],
) -> bool:
    """Return true if any evidence item is explicitly hand-authored only."""

    for item in items:
        kinds = set(source_kinds(item))
        if kinds and kinds <= CONTRACT_ONLY_KINDS and is_capability_ref(capability_ref(item)):
            return True
    return False

