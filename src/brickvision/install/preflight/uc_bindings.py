"""N145 — UC Workspace-Catalog binding pre-flight.

Per [`docs/19-local-development.md`](../../../docs/19-local-development.md)
§15.5 + [`docs/04-schemas.md`](../../../docs/04-schemas.md) §6.5.4, the
install must refuse to proceed if the BV catalog is not bound
read-write to the executing workspace. This pre-flight delegates
the resolution to ``brickvision_runtime.core.workspace`` and the
binding lookup to an injectable Databricks SDK adapter so unit tests
run offline.

The pre-flight emits a typed ``PreFlightFailure`` carrying the
``WORKSPACE_CATALOG_BINDING_MISSING`` reason code on failure;
``WRITE_TARGET_CATALOG_NOT_BOUND_RW`` is reserved for the run-time
side of the same invariant (the runner refuses to submit a Job
when the binding has been removed mid-build).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any

from brickvision_runtime.core.workspace import (
    WorkspaceIdResolver,
    get_executing_workspace_id,
)
from brickvision_runtime.failures import ReasonCode

BindingsLookup = Callable[[str], list[Mapping[str, Any]]]
"""(catalog_name) -> list of binding rows {workspace_id, binding_mode}."""


@dataclasses.dataclass(frozen=True, slots=True)
class PreFlightOutcome:
    name: str
    passed: bool
    reason_code: str | None
    detail: str
    workspace_id: str
    catalog: str


def write_target_catalog_bound_rw(
    *,
    catalog: str,
    resolver: WorkspaceIdResolver,
    bindings_lookup: BindingsLookup,
) -> PreFlightOutcome:
    """Verify that ``catalog`` is bound read-write to the executing workspace."""

    resolution = get_executing_workspace_id(resolver=resolver)
    if not resolution.success:
        return PreFlightOutcome(
            name="write_target_catalog_bound_rw",
            passed=False,
            reason_code=resolution.reason_code,
            detail=resolution.detail or "workspace id resolution failed",
            workspace_id="",
            catalog=catalog,
        )

    rows = bindings_lookup(catalog)
    matching = [
        r for r in rows if str(r.get("workspace_id")) == resolution.workspace_id
    ]
    if not matching:
        return PreFlightOutcome(
            name="write_target_catalog_bound_rw",
            passed=False,
            reason_code=ReasonCode.WORKSPACE_CATALOG_BINDING_MISSING.value,
            detail=(
                f"catalog {catalog!r} is not bound to workspace"
                f" {resolution.workspace_id!r}"
            ),
            workspace_id=resolution.workspace_id,
            catalog=catalog,
        )

    binding_mode = str(matching[-1].get("binding_mode", "")).upper()
    if binding_mode not in {"READ_WRITE", "BINDING_TYPE_READ_WRITE"}:
        return PreFlightOutcome(
            name="write_target_catalog_bound_rw",
            passed=False,
            reason_code=ReasonCode.WRITE_TARGET_CATALOG_NOT_BOUND_RW.value,
            detail=(
                f"catalog {catalog!r} bound to workspace"
                f" {resolution.workspace_id!r} as {binding_mode!r};"
                " expected READ_WRITE"
            ),
            workspace_id=resolution.workspace_id,
            catalog=catalog,
        )
    return PreFlightOutcome(
        name="write_target_catalog_bound_rw",
        passed=True,
        reason_code=None,
        detail=(
            f"catalog {catalog!r} bound READ_WRITE to workspace"
            f" {resolution.workspace_id!r}"
        ),
        workspace_id=resolution.workspace_id,
        catalog=catalog,
    )


__all__ = ["BindingsLookup", "PreFlightOutcome", "write_target_catalog_bound_rw"]
