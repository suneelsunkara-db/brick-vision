"""Workspace-catalog binding adapter contract."""

from __future__ import annotations

from collections.abc import Callable


BindingLookup = Callable[[str], bool]


def bindings_check(*, object_uri: str, lookup: BindingLookup) -> bool:
    """Return whether ``object_uri`` is writable in the executing workspace."""

    return bool(lookup(object_uri))


__all__ = ["BindingLookup", "bindings_check"]
