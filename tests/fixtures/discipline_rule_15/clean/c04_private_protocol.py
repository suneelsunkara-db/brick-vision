"""Clean 4 — private (``_``-prefixed) ``Protocol`` for structural typing.

Underscore-prefixed Protocols are exempt from the seam check because
they exist to type-erase a real third-party module's public API
(e.g. ``mlflow``, ``pandas.DataFrame`` ∪ ``pyspark.sql.DataFrame``)
where there is no Python-typed handle to import directly. They are
never satisfied by a mock at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class _MlflowModule(Protocol):
    """Structural type for the real ``mlflow`` module's public surface."""

    def start_run(self, run_name: str | None = None) -> object:
        ...

    def log_params(self, params: Mapping[str, object]) -> None:
        ...
