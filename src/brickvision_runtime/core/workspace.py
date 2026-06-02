"""N145 — workspace-id resolution.

Per [`docs/19-local-development.md`](../../../docs/19-local-development.md)
§15.5, every write-side skill must resolve the executing workspace
id before checking that the target catalog is bound read-write to
that workspace. This module is the single legal source of truth for
"what workspace are we running in?".

Rationale: hard-coding a workspace id in the .env or in skill code
breaks cross-workspace replays + makes the
``write.target.catalog.must.be.bound.read.write.to.executing.workspace``
constitutional rule un-checkable. The Databricks SDK's
``WorkspaceClient().get_workspace_id()`` is the canonical resolver;
this module wraps it through an injectable adapter so unit tests
run offline.

The audit row carries the resolved workspace id so a replay can
detect "this build was attempted on workspace W1 but the audit
shows it ran on W2" — that's a constitutional rule violation, not
a replay-tolerance question.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from brickvision_runtime.failures import ReasonCode

WorkspaceIdResolver = Callable[[], str]
"""Adapter; production wires to ``WorkspaceClient().get_workspace_id()``."""


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceResolution:
    workspace_id: str
    success: bool
    reason_code: str | None
    detail: str | None


def get_executing_workspace_id(
    *,
    resolver: WorkspaceIdResolver,
) -> WorkspaceResolution:
    """Resolve the workspace id; never raises.

    A failure surfaces as ``WORKSPACE_CATALOG_BINDING_MISSING``
    because the downstream binding check cannot proceed without an
    executing workspace id.
    """

    try:
        workspace_id = str(resolver()).strip()
    except Exception as exc:  # noqa: BLE001 — we surface as a typed reason
        return WorkspaceResolution(
            workspace_id="",
            success=False,
            reason_code=ReasonCode.WORKSPACE_CATALOG_BINDING_MISSING.value,
            detail=f"resolver raised: {exc.__class__.__name__}: {exc}",
        )
    if not workspace_id:
        return WorkspaceResolution(
            workspace_id="",
            success=False,
            reason_code=ReasonCode.WORKSPACE_CATALOG_BINDING_MISSING.value,
            detail="resolver returned empty workspace_id",
        )
    return WorkspaceResolution(
        workspace_id=workspace_id,
        success=True,
        reason_code=None,
        detail=None,
    )


__all__ = ["WorkspaceIdResolver", "WorkspaceResolution", "get_executing_workspace_id"]
