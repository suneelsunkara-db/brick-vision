"""Clean 3 — public ``Protocol`` with a real concrete subclass.

When a Protocol legitimately exists in production (e.g. to type-erase
across two real backends), it MUST have ≥1 concrete subclass that is
itself a real wrapper. Multiple real backends are fine; what's
forbidden is "Protocol + only mock subclass".
"""

from __future__ import annotations

from typing import Protocol


class StorageBackend(Protocol):
    """Two real backends (UC Volumes / Workspace Files) implement this."""

    def write_bytes(self, path: str, payload: bytes) -> None:
        ...

    def read_bytes(self, path: str) -> bytes:
        ...


class UCVolumesBackend(StorageBackend):
    """Production backend wrapping ``files.upload`` / ``files.download``.

    Explicitly subclasses ``StorageBackend`` to make the seam visible
    in source — even though Python's Protocol mechanism is structural
    and would accept this class without subclassing, BrickVision's
    discipline rule 15 prefers explicit subclassing so the
    ``NoMockOrFakeImplementations`` scorer can verify a real
    production implementation exists.
    """

    def write_bytes(self, path: str, payload: bytes) -> None:
        raise NotImplementedError("wired to databricks.sdk.files.upload")

    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError("wired to databricks.sdk.files.download")


class WorkspaceFilesBackend(StorageBackend):
    """Production backend wrapping the Workspace Files REST API."""

    def write_bytes(self, path: str, payload: bytes) -> None:
        raise NotImplementedError("wired to /api/2.0/workspace/import")

    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError("wired to /api/2.0/workspace/export")
