"""Shared Workspace KG visibility policy."""

from __future__ import annotations

from . import databricks_sql

INTERNAL_UC_TABLE_PREFIXES: tuple[str, ...] = ("__materialization_mat_",)


def workspace_claim_visibility_where() -> str:
    subject_table_name = "split_part(subject, '.', 3)"
    object_table_name = "split_part(object_ref, '.', 3)"
    internal_subject_filters = [
        (
            "NOT (subject LIKE 'table:%%' "
            f"AND LEFT({subject_table_name}, {len(prefix)}) = "
            f"{databricks_sql.sql_string_literal(prefix)})"
        )
        for prefix in INTERNAL_UC_TABLE_PREFIXES
    ]
    internal_object_filters = [
        (
            "(object_ref IS NULL OR NOT (object_ref LIKE 'column:%%' "
            f"AND LEFT({object_table_name}, {len(prefix)}) = "
            f"{databricks_sql.sql_string_literal(prefix)}))"
        )
        for prefix in INTERNAL_UC_TABLE_PREFIXES
    ]
    return " AND ".join([*internal_subject_filters, *internal_object_filters])

