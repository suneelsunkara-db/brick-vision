"""Human-in-the-loop approval contract."""

from __future__ import annotations

import dataclasses
import time
import uuid
from typing import Any


@dataclasses.dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    subject: str
    reason: str
    metadata: dict[str, Any]
    created_at_ms: int


def request_approval(
    *,
    subject: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> ApprovalRequest:
    """Create a deterministic approval request payload for a HITL adapter."""

    return ApprovalRequest(
        approval_id=f"approval_{uuid.uuid4().hex[:12]}",
        subject=subject,
        reason=reason,
        metadata=dict(metadata or {}),
        created_at_ms=int(time.time() * 1000),
    )


__all__ = ["ApprovalRequest", "request_approval"]
