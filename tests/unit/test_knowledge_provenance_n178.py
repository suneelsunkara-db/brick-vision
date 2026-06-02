"""N178 BULK — provenance-pane backend shape contract.

The right-rail provenance pane on ``apps/console/src/routes/knowledge.tsx``
reads from ``GET /api/knowledge/extensions/{extension_id}/provenance``,
which is backed by ``console_api.runtime_bridge.get_extension_provenance``.

This suite pins the shape contract of the runtime-bridge function
**both** in C.1 SHELL (no active snapshot) and in active-snapshot
mode. The pane components depend on every key in the payload being
present so they can render their structured sections without
existence checks scattered across the JSX.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The runtime_bridge lives under ``apps/console-api/src/`` which is not
# on the default test pythonpath; add it explicitly here.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CONSOLE_API_SRC = _REPO_ROOT / "apps" / "console-api" / "src"
if str(_CONSOLE_API_SRC) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_API_SRC))


from console_api.runtime_bridge import get_extension_provenance  # noqa: E402


_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "extension_id",
        "label",
        "parent_meta_skill",
        "effect_class",
        "cloud_variance",
        "authority_score",
        "authority_scorer",
        "cross_cloud_note",
        "contributing_chunks",
        "two_hop_neighbors",
    }
)


@pytest.fixture(autouse=True)
def _isolate_active_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to "indexer has not yet run" for every test.

    The runtime bridge resolves the active snapshot by reading
    ``active_snapshot_id_synced`` from Lakebase Postgres; with no
    Lakebase env vars set, ``_active_snapshot()`` returns ``None`` on
    its own. We additionally monkeypatch the bridge's resolver to
    short-circuit it (no SDK / psycopg import path), and tests that
    need an active snapshot opt-in by re-monkeypatching it.
    """

    from console_api import runtime_bridge as bridge

    monkeypatch.setattr(bridge, "_active_snapshot", lambda: None)
    monkeypatch.delenv("BV_LAKEBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("BV_LAKEBASE_DATABASE", raising=False)
    monkeypatch.delenv("BV_DRY_RUN", raising=False)


def test_provenance_shape_pin_shell_returns_all_required_keys() -> None:
    """Even on a bootstrap install (no active snapshot) the pane must
    receive every required key so it can render the structured
    sections without per-field existence checks."""

    payload = get_extension_provenance(
        user_id="user-1", extension_id="meta:delta-lake/ext:create-table"
    )
    missing = _REQUIRED_KEYS - set(payload.keys())
    assert missing == set(), f"missing keys: {sorted(missing)}"


def test_provenance_shell_carries_indexer_not_run_banner() -> None:
    payload = get_extension_provenance(
        user_id="user-1", extension_id="meta:unity-catalog/ext:list-catalogs"
    )
    assert payload["indexer_state"] == "never_run"
    assert "indexer has not yet produced an active snapshot" in payload["message"]


def test_provenance_shell_returns_empty_lists_for_chunks_and_neighbors() -> None:
    """The pane components branch on ``length === 0`` to render the
    empty-state hint; the bridge must guarantee a list (not ``None``)."""

    payload = get_extension_provenance(
        user_id="user-1", extension_id="meta:mlflow/ext:register-model"
    )
    assert isinstance(payload["contributing_chunks"], list)
    assert payload["contributing_chunks"] == []
    assert isinstance(payload["two_hop_neighbors"], list)
    assert payload["two_hop_neighbors"] == []


def test_provenance_shell_cross_cloud_note_is_none() -> None:
    """The pane only renders the cross-cloud callout when
    ``cross_cloud_note`` is non-null — see knowledge.tsx
    ``CrossCloudNoteSection``."""

    payload = get_extension_provenance(
        user_id="user-1", extension_id="meta:vector-search/ext:create-endpoint"
    )
    assert payload["cross_cloud_note"] is None


def test_provenance_shell_metadata_fields_default_to_none() -> None:
    """Identity + Authority sections render ``Placeholder`` ('—') when
    these are ``None``; the bridge must not return undefined / missing
    keys."""

    payload = get_extension_provenance(
        user_id="user-1", extension_id="meta:lakeflow/ext:create-job"
    )
    for key in (
        "label",
        "parent_meta_skill",
        "effect_class",
        "cloud_variance",
        "authority_score",
        "authority_scorer",
    ):
        assert payload[key] is None, f"{key} expected None in SHELL"


def test_provenance_active_snapshot_reflected_when_snapshot_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bridge's ``_active_snapshot()`` returns a snapshot, the
    payload surfaces ``indexer_state=active`` plus the snapshot id and
    promoted timestamp so the pane footer can render them.

    We additionally stub the per-table query helpers (``_query_one`` /
    ``_query_all``) so the bridge doesn't try to dial Lakebase Postgres
    in CI — the contracts pinned here are payload-shape only, not the
    SQL itself (which lives in ``test_runtime_bridge_lakebase.py``).
    """

    from console_api import runtime_bridge as bridge

    monkeypatch.setattr(
        bridge, "_active_snapshot", lambda: ("snap_n178_test", 1_730_000_000_000),
    )
    monkeypatch.setattr(bridge, "_query_one", lambda *a, **k: None)
    monkeypatch.setattr(bridge, "_query_all", lambda *a, **k: [])

    payload = get_extension_provenance(
        user_id="user-1",
        extension_id="meta:delta-lake/ext:create-table",
    )
    assert payload["indexer_state"] == "active"
    assert payload["active_snapshot_id"] == "snap_n178_test"
    assert payload["promoted_at_ms"] == 1_730_000_000_000
    # The extension row itself wasn't found (stubbed _query_one returns
    # None); contributing_chunks + two_hop_neighbors stay empty.
    assert payload["contributing_chunks"] == []
    assert payload["two_hop_neighbors"] == []


def test_provenance_shell_extension_id_is_echoed_back() -> None:
    """The pane uses ``data.extension_id`` to compute its query key
    + render the title. The bridge must round-trip the input id
    verbatim so the pane never shows a different id than was clicked.
    """

    eid = "meta:lakeflow-jobs/ext:run-job-now"
    payload = get_extension_provenance(user_id="user-1", extension_id=eid)
    assert payload["extension_id"] == eid


def test_provenance_handles_special_characters_in_extension_id() -> None:
    """The TSX uses ``encodeURIComponent`` on the extension id; the
    bridge must accept the decoded form (with ``/`` and ``:`` and
    underscores) without erroring or trimming."""

    eid = "meta:unity_catalog-foundation/ext:list_catalogs"
    payload = get_extension_provenance(user_id="user-1", extension_id=eid)
    assert payload["extension_id"] == eid


def test_provenance_router_endpoint_wires_through() -> None:
    """The FastAPI route at
    ``GET /api/knowledge/extensions/{extension_id}/provenance``
    delegates to ``runtime_bridge.get_extension_provenance``.

    We can't import the FastAPI router itself in offline CI because
    ``fastapi`` is a sidecar-only dependency; instead we verify the
    on-disk router file references both the URL pattern and the
    bridge function name — a structural pin that catches a removed
    or renamed wiring without dragging in the SPA dependencies.
    """

    router_path = (
        _REPO_ROOT
        / "apps"
        / "console-api"
        / "src"
        / "console_api"
        / "routers"
        / "knowledge.py"
    )
    body = router_path.read_text(encoding="utf-8")
    assert "/extensions/{extension_id}/provenance" in body, (
        "expected the provenance route path to be wired in knowledge router"
    )
    assert "get_extension_provenance" in body, (
        "expected the route to delegate to runtime_bridge.get_extension_provenance"
    )
