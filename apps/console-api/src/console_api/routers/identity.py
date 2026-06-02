"""``/api/me`` — current OBO identity (SPA-safe)."""

from __future__ import annotations

from fastapi import APIRouter

from ..identity import CurrentUser

router = APIRouter(tags=["identity"])


@router.get("/api/me")
def me(user: CurrentUser) -> dict[str, str | None]:
    """Return the user payload the top bar reads on boot.

    Note: the OBO token itself is never returned. The SPA only
    needs the display name + email to render avatars / mentions.
    """

    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "email": user.email,
    }
