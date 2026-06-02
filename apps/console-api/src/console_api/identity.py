"""OBO identity extraction + 401 handling.

Per ``docs/16-identity-audit-replay.md`` §12.4, the OBO token must
**never** leave the sidecar process. The SPA only sees the user's
display name and email; the token itself stays attached to the
per-request workspace client.

In production, the partner deploys the sidecar inside a Databricks
App or a customer-managed container where the OBO token is injected
as ``X-Forwarded-Access-Token`` (Databricks Apps) or comparable
header. For local dev we fall through to the developer's
``DATABRICKS_HOST`` + ``DATABRICKS_TOKEN`` profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """SPA-safe identity payload.

    The OBO token itself is intentionally **not** exposed here — the
    workspace client lives behind ``get_workspace_client``.
    """

    user_id: str
    display_name: str
    email: str | None


def _obo_token_expired_error() -> HTTPException:
    """Construct the canonical 401 the SPA's auth guard expects."""

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "reason_code": "OBO_TOKEN_EXPIRED",
            "message": (
                "OBO token missing or expired; refresh the page to "
                "re-authenticate."
            ),
        },
    )


def get_user_identity(
    x_forwarded_email: Annotated[str | None, Header(alias="X-Forwarded-Email")] = None,
    x_forwarded_user: Annotated[str | None, Header(alias="X-Forwarded-User")] = None,
    x_forwarded_preferred_username: Annotated[
        str | None, Header(alias="X-Forwarded-Preferred-Username")
    ] = None,
) -> UserIdentity:
    """FastAPI dependency: resolve the request's OBO identity.

    Priority order (Databricks Apps convention):

    1. ``X-Forwarded-Email`` (preferred)
    2. ``X-Forwarded-User``
    3. ``X-Forwarded-Preferred-Username``

    For local development without a real OBO header, returns a
    deterministic ``dev@local`` identity so the SPA can boot — but
    every audit row carries that fallback so it's obvious in
    replay.
    """

    candidate = x_forwarded_email or x_forwarded_user or x_forwarded_preferred_username
    if candidate is None:
        return UserIdentity(
            user_id="dev@local",
            display_name="Local developer",
            email="dev@local",
        )
    return UserIdentity(
        user_id=candidate,
        display_name=candidate.split("@")[0],
        email=candidate if "@" in candidate else None,
    )


CurrentUser = Annotated[UserIdentity, Depends(get_user_identity)]
