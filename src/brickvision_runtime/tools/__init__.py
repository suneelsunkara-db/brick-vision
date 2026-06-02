"""UC function decorators for skill authoring.

Per `runtime/CONVENTIONS.md` (N0-1). The decorators are metadata-only at
build time; the install CLI (`brickvision install`) reads the decorator
kwargs to register the function in UC.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_REGISTRY_SQL: dict[str, "UcFunctionSqlSpec"] = {}
_REGISTRY_PY: dict[str, "UcFunctionPythonSpec"] = {}


@dataclass(frozen=True)
class UcFunctionSqlSpec:
    securable: str
    returns: str
    sql_security: str
    parameters: tuple[tuple[str, str], ...]
    body_loader: Callable[[], str]


@dataclass(frozen=True)
class UcFunctionPythonSpec:
    securable: str
    returns: str
    fn: Callable[..., Any]


def uc_function_sql(
    *,
    securable: str,
    returns: str,
    sql_security: str = "DEFINER",
    parameters: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
) -> Callable[[Callable[[], str]], Callable[[], str]]:
    """Mark a Python function as a SQL UC function. Body returns the SQL DDL."""

    def deco(fn: Callable[[], str]) -> Callable[[], str]:
        spec = UcFunctionSqlSpec(
            securable=securable,
            returns=returns,
            sql_security=sql_security,
            parameters=tuple(tuple(p) for p in parameters),
            body_loader=fn,
        )
        _REGISTRY_SQL[securable] = spec
        return fn

    return deco


def uc_function_python(
    *,
    securable: str,
    returns: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a Python function as a Python UC function."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY_PY[securable] = UcFunctionPythonSpec(
            securable=securable, returns=returns, fn=fn,
        )
        return fn

    return deco


def list_registered_sql() -> list[UcFunctionSqlSpec]:
    return list(_REGISTRY_SQL.values())


def list_registered_python() -> list[UcFunctionPythonSpec]:
    return list(_REGISTRY_PY.values())


__all__ = [
    "UcFunctionPythonSpec",
    "UcFunctionSqlSpec",
    "list_registered_python",
    "list_registered_sql",
    "uc_function_python",
    "uc_function_sql",
]
