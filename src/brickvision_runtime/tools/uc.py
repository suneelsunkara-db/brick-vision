"""Read-only Unity Catalog introspection tools."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def list_catalogs(*, include_system: bool = False) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_catalogs``."""

    if _is_dry_run():
        rows = _read_fixture_rows("catalogs")
    else:
        rows = _execute_select(
            """
            SELECT catalog_name, catalog_owner, comment, created
            FROM system.information_schema.catalogs
            """
        )
    if include_system:
        return rows
    return [row for row in rows if row.get("catalog_name") != "system"]


def list_schemas(*, catalog_name: str) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_schemas``."""

    if _is_dry_run():
        rows = [
            row
            for row in _read_fixture_rows("schemas")
            if row.get("catalog_name") == catalog_name
        ]
    else:
        rows = _execute_select(
            f"""
            SELECT catalog_name, schema_name, schema_owner, comment, created
            FROM system.information_schema.schemata
            WHERE catalog_name = {_sql_string_literal(catalog_name)}
            """
        )
    return [
        row for row in rows
        if not _is_internal_workspace_schema(
            catalog_name=str(row.get("catalog_name", "")),
            schema_name=str(row.get("schema_name", "")),
        )
    ]


def list_tables(*, catalog_name: str, schema_name: str) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_tables``."""

    if _is_dry_run():
        rows = [
            row
            for row in _read_fixture_rows("tables")
            if row.get("table_catalog") == catalog_name
            and row.get("table_schema") == schema_name
        ]
    else:
        rows = _execute_select(
            f"""
            SELECT table_catalog, table_schema, table_name, table_type,
                   data_source_format, table_owner, created, last_altered, comment
            FROM system.information_schema.tables
            WHERE table_catalog = {_sql_string_literal(catalog_name)}
              AND table_schema = {_sql_string_literal(schema_name)}
            """
        )
    return [
        row for row in rows
        if not _is_internal_workspace_object(
            catalog_name=catalog_name,
            schema_name=schema_name,
            object_name=row.get("table_name"),
        )
    ]


def list_views(*, catalog_name: str, schema_name: str) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_views``."""

    if _is_dry_run():
        rows = [
            row
            for row in _read_fixture_rows("views")
            if row.get("table_catalog") == catalog_name
            and row.get("table_schema") == schema_name
        ]
    else:
        rows = _execute_select(
            f"""
            SELECT table_catalog, table_schema, table_name,
                   sha2(view_definition, 256) AS view_definition_hash
            FROM system.information_schema.views
            WHERE table_catalog = {_sql_string_literal(catalog_name)}
              AND table_schema = {_sql_string_literal(schema_name)}
            """
        )
    return [
        row for row in rows
        if not _is_internal_workspace_object(
            catalog_name=catalog_name,
            schema_name=schema_name,
            object_name=row.get("table_name"),
        )
    ]


def list_volumes(*, catalog_name: str, schema_name: str) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_volumes``."""

    if _is_dry_run():
        return [
            row
            for row in _read_fixture_rows("volumes")
            if row.get("volume_catalog") == catalog_name
            and row.get("volume_schema") == schema_name
        ]
    return _execute_select(
        f"""
        SELECT volume_catalog, volume_schema, volume_name, volume_type, volume_owner
        FROM system.information_schema.volumes
        WHERE volume_catalog = {_sql_string_literal(catalog_name)}
          AND volume_schema = {_sql_string_literal(schema_name)}
        """
    )


def list_functions(*, catalog_name: str, schema_name: str) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_functions``."""

    if _is_dry_run():
        return [
            row
            for row in _read_fixture_rows("functions")
            if row.get("routine_catalog") == catalog_name
            and row.get("routine_schema") == schema_name
        ]
    return _execute_select(
        f"""
        SELECT routine_catalog, routine_schema, routine_name,
               external_language, data_type AS return_type, routine_owner
        FROM system.information_schema.routines
        WHERE routine_catalog = {_sql_string_literal(catalog_name)}
          AND routine_schema = {_sql_string_literal(schema_name)}
        """
    )


def list_columns(*, catalog_name: str, schema_name: str, table_name: str) -> list[dict[str, Any]]:
    """Implementation of ``tool:uc.list_columns``."""

    if _is_dry_run():
        return [
            row
            for row in _read_fixture_rows("columns")
            if row.get("table_catalog") == catalog_name
            and row.get("table_schema") == schema_name
            and row.get("table_name") == table_name
        ]
    return _execute_select(
        f"""
        SELECT table_catalog, table_schema, table_name, column_name,
               ordinal_position, data_type, is_nullable, comment
        FROM system.information_schema.columns
        WHERE table_catalog = {_sql_string_literal(catalog_name)}
          AND table_schema = {_sql_string_literal(schema_name)}
          AND table_name = {_sql_string_literal(table_name)}
        ORDER BY ordinal_position
        """
    )


def profile_table(
    *,
    catalog_name: str,
    schema_name: str,
    table_name: str,
    max_columns: int = 20,
) -> dict[str, Any] | None:
    """Profile one UC table using exact counts over a bounded column slice."""

    if _is_dry_run():
        profiles = _read_fixture_mapping("table_profiles")
        profile = profiles.get(f"{catalog_name}.{schema_name}.{table_name}")
        return dict(profile) if isinstance(profile, Mapping) else None

    columns = list_columns(
        catalog_name=catalog_name,
        schema_name=schema_name,
        table_name=table_name,
    )
    profiled_columns = [
        row for row in columns
        if _is_profile_supported_type(str(row.get("data_type", "")))
    ][:max_columns]
    table_ref = _qualified_table_ref(catalog_name, schema_name, table_name)
    row_count_rows = _execute_select(f"SELECT COUNT(*) AS row_count FROM {table_ref}")
    row_count = int(row_count_rows[0].get("row_count", 0)) if row_count_rows else 0

    null_counts: dict[str, int] = {}
    distinct_counts: dict[str, int] = {}
    if profiled_columns:
        expressions: list[str] = []
        for row in profiled_columns:
            column_name = str(row.get("column_name", ""))
            if not column_name:
                continue
            column_ref = _quote_identifier(column_name)
            alias_prefix = _safe_alias(column_name)
            expressions.append(
                f"COUNT_IF({column_ref} IS NULL) AS {_quote_identifier(alias_prefix + '__null_count')}"
            )
            expressions.append(
                f"COUNT(DISTINCT {column_ref}) AS {_quote_identifier(alias_prefix + '__distinct_count')}"
            )
        if expressions:
            metric_rows = _execute_select(
                f"SELECT {', '.join(expressions)} FROM {table_ref}"
            )
            metrics = metric_rows[0] if metric_rows else {}
            for row in profiled_columns:
                column_name = str(row.get("column_name", ""))
                alias_prefix = _safe_alias(column_name)
                null_counts[column_name] = int(metrics.get(f"{alias_prefix}__null_count", 0))
                distinct_counts[column_name] = int(
                    metrics.get(f"{alias_prefix}__distinct_count", 0)
                )

    return {
        "table_catalog": catalog_name,
        "table_schema": schema_name,
        "table_name": table_name,
        "row_count": row_count,
        "columns": columns,
        "profiled_columns": profiled_columns,
        "null_counts": null_counts,
        "distinct_counts": distinct_counts,
    }


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "").lower() in ("1", "true", "yes")


def _resolve_fixture_path() -> Path:
    raw = os.environ.get("BV_DRY_RUN_UC_INTROSPECTION_PATH", "").strip()
    if raw:
        return Path(raw)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "tests" / "fixtures" / "kg" / "uc_introspection.json"


def _read_fixture_rows(key: str) -> list[dict[str, Any]]:
    target = _resolve_fixture_path()
    if not target.exists():
        raise RuntimeError(
            f"dry-run UC introspection fixture missing: {target}"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get(key, [])
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _read_fixture_mapping(key: str) -> dict[str, Any]:
    target = _resolve_fixture_path()
    if not target.exists():
        raise RuntimeError(
            f"dry-run UC introspection fixture missing: {target}"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    value = payload.get(key, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _resolve_warehouse_id() -> str:
    warehouse_id = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
        or ""
    ).strip()
    if not warehouse_id:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is required for UC tools")
    return warehouse_id


def _execute_select(statement: str) -> list[dict[str, Any]]:
    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

    client = WorkspaceClient()
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=_resolve_warehouse_id(),
        wait_timeout="50s",
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")
    result = response.result
    if result is None:
        return []
    manifest = getattr(response, "manifest", None)
    schema = getattr(manifest, "schema", None) if manifest else None
    columns = getattr(schema, "columns", None) if schema else None
    column_names = [str(getattr(col, "name", "")) for col in (columns or [])]
    rows = getattr(result, "data_array", None) or []
    return [dict(zip(column_names, row, strict=False)) for row in rows]


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _qualified_table_ref(catalog_name: str, schema_name: str, table_name: str) -> str:
    return ".".join(
        (
            _quote_identifier(catalog_name),
            _quote_identifier(schema_name),
            _quote_identifier(table_name),
        )
    )


def _safe_alias(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)


def _is_profile_supported_type(data_type: str) -> bool:
    normalized = data_type.lower()
    unsupported = ("array", "map", "struct", "variant", "binary")
    return bool(normalized) and not any(token in normalized for token in unsupported)


def _is_internal_workspace_object(
    *, catalog_name: str, schema_name: str, object_name: Any,  # noqa: ANN401
) -> bool:
    object_name_text = str(object_name or "")
    if object_name_text.startswith("__materialization_mat_"):
        return True
    if object_name_text.startswith(("bv_profile_quality_", "bv_schema_profile_quality_")):
        return True
    return (
        _is_internal_workspace_schema(
            catalog_name=catalog_name,
            schema_name=schema_name,
        )
    )


def _is_internal_workspace_schema(*, catalog_name: str, schema_name: str) -> bool:
    return (
        catalog_name == os.environ.get("BV_CATALOG", "brickvision")
        and schema_name == os.environ.get("BV_SCHEMA", "brickvision")
    )


__all__ = [
    "list_catalogs",
    "list_columns",
    "list_functions",
    "list_schemas",
    "list_tables",
    "list_views",
    "list_volumes",
    "profile_table",
]
