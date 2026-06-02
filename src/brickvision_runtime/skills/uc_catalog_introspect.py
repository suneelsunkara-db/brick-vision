"""Executable runtime for ``skill:uc.catalog-introspect``."""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Any

from brickvision_runtime.core import time as bv_time
from brickvision_runtime.kg.claims import WorkspaceClaim, emit_claims
from brickvision_runtime.tools import uc


_SKILL_ID = "skill:uc.catalog-introspect"


@dataclasses.dataclass(frozen=True, slots=True)
class UcCatalogIntrospectResult:
    """Summary returned by the mechanical UC introspection skill."""

    catalogs_seen: int
    schemas_seen: int
    tables_seen: int
    views_seen: int
    volumes_seen: int
    functions_seen: int
    tables_profiled: int
    claims_emitted: int
    duration_ms: int
    dry_run: bool


def run_uc_catalog_introspect(
    *,
    workspace_profile_id: str | None = None,
    workspace_id: str | None = None,
    include_system: bool = False,
    catalog_filter: str | None = None,
    allowed_catalogs: tuple[str, ...] = (),
    blocked_catalogs: tuple[str, ...] = (),
    config_hash: str | None = None,
    run_id: str | None = None,
) -> UcCatalogIntrospectResult:
    """Inspect Unity Catalog structure and emit Workspace KG claims."""

    started_at_ms = bv_time.now_ms()
    profile_id = workspace_profile_id or os.environ.get(
        "BV_ACTIVE_WORKSPACE_PROFILE", "default"
    )
    effective_workspace_id = workspace_id or os.environ.get("DATABRICKS_WORKSPACE_ID")

    catalogs = _filter_catalogs(
        uc.list_catalogs(include_system=include_system),
        catalog_filter=catalog_filter,
        allowed_catalogs=allowed_catalogs,
        blocked_catalogs=blocked_catalogs,
    )
    schemas: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    table_profiles: list[dict[str, Any]] = []

    for catalog in catalogs:
        catalog_name = str(catalog.get("catalog_name", ""))
        if not catalog_name:
            continue
        catalog_schemas = uc.list_schemas(catalog_name=catalog_name)
        schemas.extend(catalog_schemas)
        for schema in catalog_schemas:
            schema_name = str(schema.get("schema_name", ""))
            if not schema_name:
                continue
            tables.extend(
                uc.list_tables(catalog_name=catalog_name, schema_name=schema_name)
            )
            views.extend(
                uc.list_views(catalog_name=catalog_name, schema_name=schema_name)
            )
            volumes.extend(
                uc.list_volumes(catalog_name=catalog_name, schema_name=schema_name)
            )
            functions.extend(
                uc.list_functions(catalog_name=catalog_name, schema_name=schema_name)
            )

    max_profile_tables = _env_int("BV_WORKSPACE_KG_PROFILE_MAX_TABLES", default=25)
    max_profile_columns = _env_int("BV_WORKSPACE_KG_PROFILE_MAX_COLUMNS", default=20)
    for table in tables[:max_profile_tables]:
        catalog_name = str(table.get("table_catalog", ""))
        schema_name = str(table.get("table_schema", ""))
        table_name = str(table.get("table_name", ""))
        if not catalog_name or not schema_name or not table_name:
            continue
        try:
            profile = uc.profile_table(
                catalog_name=catalog_name,
                schema_name=schema_name,
                table_name=table_name,
                max_columns=max_profile_columns,
            )
        except Exception:  # noqa: BLE001
            profile = None
        if profile:
            table_profiles.append(profile)

    observed_at_ms = bv_time.now_ms()
    claims = _build_claims(
        workspace_profile_id=profile_id,
        workspace_id=effective_workspace_id,
        observed_at_ms=observed_at_ms,
        config_hash=config_hash,
        run_id=run_id,
        catalogs=catalogs,
        schemas=schemas,
        tables=tables,
        views=views,
        volumes=volumes,
        functions=functions,
        table_profiles=table_profiles,
    )
    emit_result = emit_claims(claims=claims)
    completed_at_ms = bv_time.now_ms()

    return UcCatalogIntrospectResult(
        catalogs_seen=len(catalogs),
        schemas_seen=len(schemas),
        tables_seen=len(tables),
        views_seen=len(views),
        volumes_seen=len(volumes),
        functions_seen=len(functions),
        tables_profiled=len(table_profiles),
        claims_emitted=emit_result.claims_emitted,
        duration_ms=max(0, completed_at_ms - started_at_ms),
        dry_run=emit_result.dry_run,
    )


def _filter_catalogs(
    catalogs: list[dict[str, Any]],
    *,
    catalog_filter: str | None,
    allowed_catalogs: tuple[str, ...],
    blocked_catalogs: tuple[str, ...],
) -> list[dict[str, Any]]:
    allowed = {name for name in allowed_catalogs if name}
    blocked = {name for name in blocked_catalogs if name}
    filtered = catalogs
    if allowed:
        filtered = [
            row for row in filtered
            if str(row.get("catalog_name", "")) in allowed
        ]
    if blocked:
        filtered = [
            row for row in filtered
            if str(row.get("catalog_name", "")) not in blocked
        ]
    if not catalog_filter:
        return filtered
    return [
        row
        for row in filtered
        if str(row.get("catalog_name", "")).startswith(catalog_filter.rstrip("%"))
    ]


def _build_claims(
    *,
    workspace_profile_id: str,
    workspace_id: str | None,
    observed_at_ms: int,
    config_hash: str | None,
    run_id: str | None,
    catalogs: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    views: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    table_profiles: list[dict[str, Any]] | None = None,
) -> list[WorkspaceClaim]:
    claims: list[WorkspaceClaim] = []
    base = {
        "workspace_profile_id": workspace_profile_id,
        "workspace_id": workspace_id,
        "source_skill_id": _SKILL_ID,
        "source_tool_id": "tool:kg.emit_claims",
        "observed_at_ms": observed_at_ms,
        "config_hash": config_hash,
        "run_id": run_id,
    }

    for row in catalogs:
        catalog_name = str(row.get("catalog_name", ""))
        if not catalog_name:
            continue
        claims.append(
            WorkspaceClaim(
                **base,
                subject=f"catalog:{catalog_name}",
                subject_kind="CATALOG",
                predicate="EXISTS",
                value_json=_json({"exists": True}),
                metadata_json=_json(_compact(row)),
            )
        )

    for row in schemas:
        catalog_name = str(row.get("catalog_name", ""))
        schema_name = str(row.get("schema_name", ""))
        if not catalog_name or not schema_name:
            continue
        claims.append(
            WorkspaceClaim(
                **base,
                subject=f"schema:{catalog_name}.{schema_name}",
                subject_kind="SCHEMA",
                predicate="BELONGS_TO",
                object_ref=f"catalog:{catalog_name}",
                value_json=_json({"parent": f"catalog:{catalog_name}"}),
                metadata_json=_json(_compact(row)),
            )
        )

    for row in tables:
        catalog_name = str(row.get("table_catalog", ""))
        schema_name = str(row.get("table_schema", ""))
        table_name = str(row.get("table_name", ""))
        if not catalog_name or not schema_name or not table_name:
            continue
        claims.append(
            WorkspaceClaim(
                **base,
                subject=f"table:{catalog_name}.{schema_name}.{table_name}",
                subject_kind="TABLE",
                predicate="BELONGS_TO",
                object_ref=f"schema:{catalog_name}.{schema_name}",
                value_json=_json({"parent": f"schema:{catalog_name}.{schema_name}"}),
                metadata_json=_json(_compact(row)),
            )
        )

    for row in table_profiles or []:
        catalog_name = str(row.get("table_catalog", ""))
        schema_name = str(row.get("table_schema", ""))
        table_name = str(row.get("table_name", ""))
        subject = f"table:{catalog_name}.{schema_name}.{table_name}"
        if not catalog_name or not schema_name or not table_name:
            continue
        columns = [col for col in row.get("columns", []) if isinstance(col, dict)]
        null_counts = {
            str(key): int(value)
            for key, value in dict(row.get("null_counts", {})).items()
        }
        distinct_counts = {
            str(key): int(value)
            for key, value in dict(row.get("distinct_counts", {})).items()
        }
        row_count = int(row.get("row_count", 0))
        claims.append(
            WorkspaceClaim(
                **base,
                subject=subject,
                subject_kind="TABLE",
                predicate="ROW_COUNT",
                value_json=_json({"row_count": row_count}),
                metadata_json=_json(_compact(row)),
            )
        )
        for column in columns:
            column_name = str(column.get("column_name", ""))
            if not column_name:
                continue
            claims.append(
                WorkspaceClaim(
                    **base,
                    subject=subject,
                    subject_kind="TABLE",
                    predicate="HAS_COLUMN",
                    object_ref=f"column:{catalog_name}.{schema_name}.{table_name}.{column_name}",
                    value_json=_json(_compact(column)),
                    metadata_json=_json({"profiled": column_name in null_counts}),
                )
            )
            if column_name in null_counts:
                claims.append(
                    WorkspaceClaim(
                        **base,
                        subject=subject,
                        subject_kind="TABLE",
                        predicate="NULL_COUNT",
                        object_ref=(
                            f"column:{catalog_name}.{schema_name}.{table_name}.{column_name}"
                        ),
                        value_json=_json(
                            {
                                "column": column_name,
                                "null_count": null_counts[column_name],
                            }
                        ),
                    )
                )
            if column_name in distinct_counts:
                claims.append(
                    WorkspaceClaim(
                        **base,
                        subject=subject,
                        subject_kind="TABLE",
                        predicate="DISTINCT_COUNT",
                        object_ref=(
                            f"column:{catalog_name}.{schema_name}.{table_name}.{column_name}"
                        ),
                        value_json=_json(
                            {
                                "column": column_name,
                                "distinct_count": distinct_counts[column_name],
                            }
                        ),
                    )
                )
        grain_candidates = sorted(
            column_name
            for column_name, distinct_count in distinct_counts.items()
            if row_count > 0
            and distinct_count == row_count
            and null_counts.get(column_name, 1) == 0
        )
        claims.append(
            WorkspaceClaim(
                **base,
                subject=subject,
                subject_kind="TABLE",
                predicate="GRAIN_CHECK",
                value_json=_json(
                    {
                        "row_count": row_count,
                        "candidate_key_columns": grain_candidates,
                        "has_single_column_candidate_key": bool(grain_candidates),
                    }
                ),
            )
        )

    for row in views:
        catalog_name = str(row.get("table_catalog", ""))
        schema_name = str(row.get("table_schema", ""))
        view_name = str(row.get("table_name", ""))
        if not catalog_name or not schema_name or not view_name:
            continue
        claims.append(
            WorkspaceClaim(
                **base,
                subject=f"view:{catalog_name}.{schema_name}.{view_name}",
                subject_kind="VIEW",
                predicate="BELONGS_TO",
                object_ref=f"schema:{catalog_name}.{schema_name}",
                value_json=_json({"parent": f"schema:{catalog_name}.{schema_name}"}),
                metadata_json=_json(_compact(row)),
            )
        )

    for row in volumes:
        catalog_name = str(row.get("volume_catalog", ""))
        schema_name = str(row.get("volume_schema", ""))
        volume_name = str(row.get("volume_name", ""))
        if not catalog_name or not schema_name or not volume_name:
            continue
        claims.append(
            WorkspaceClaim(
                **base,
                subject=f"volume:{catalog_name}.{schema_name}.{volume_name}",
                subject_kind="VOLUME",
                predicate="BELONGS_TO",
                object_ref=f"schema:{catalog_name}.{schema_name}",
                value_json=_json({"parent": f"schema:{catalog_name}.{schema_name}"}),
                metadata_json=_json(_compact(row)),
            )
        )

    for row in functions:
        catalog_name = str(row.get("routine_catalog", ""))
        schema_name = str(row.get("routine_schema", ""))
        function_name = str(row.get("routine_name", ""))
        if not catalog_name or not schema_name or not function_name:
            continue
        claims.append(
            WorkspaceClaim(
                **base,
                subject=f"function:{catalog_name}.{schema_name}.{function_name}",
                subject_kind="FUNCTION",
                predicate="BELONGS_TO",
                object_ref=f"schema:{catalog_name}.{schema_name}",
                value_json=_json({"parent": f"schema:{catalog_name}.{schema_name}"}),
                metadata_json=_json(_compact(row)),
            )
        )

    return claims


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in row.items() if v not in (None, "")}


def _json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


__all__ = ["UcCatalogIntrospectResult", "run_uc_catalog_introspect"]
