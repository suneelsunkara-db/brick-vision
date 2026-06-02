"""Workspace KG claim writer and dry-run reader."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from brickvision_runtime.core import time as bv_time


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceClaim:
    """One observed fact about the active partner workspace."""

    workspace_profile_id: str
    subject: str
    subject_kind: str
    predicate: str
    value_json: str
    source_skill_id: str
    observed_at_ms: int
    claim_id: str = ""
    workspace_id: str | None = None
    object_ref: str | None = None
    metadata_json: str | None = None
    source_tool_id: str | None = None
    confidence: float = 1.0
    emitted_at_ms: int = 0
    config_hash: str | None = None
    run_id: str | None = None

    def normalized(self) -> "WorkspaceClaim":
        """Fill deterministic id + emitted timestamp."""

        emitted = self.emitted_at_ms or bv_time.now_ms()
        claim_id = self.claim_id or _stable_claim_id(self)
        return dataclasses.replace(self, claim_id=claim_id, emitted_at_ms=emitted)


@dataclasses.dataclass(frozen=True, slots=True)
class EmitClaimsResult:
    """Result of one ``tool:kg.emit_claims`` invocation."""

    table_name: str
    claims_emitted: int
    dry_run: bool
    current_table_name: str | None = None
    dry_run_log_path: str | None = None


def emit_claims(
    *,
    claims: Sequence[WorkspaceClaim],
    catalog: str | None = None,
    schema: str | None = None,
) -> EmitClaimsResult:
    """Append workspace claims to ``workspace_claims``.

    ``BV_DRY_RUN=true`` writes the exact payload to a JSON fixture instead of
    touching the workspace. The live path uses Databricks Statement Execution.
    """

    resolved_catalog = catalog or _resolve_catalog()
    table_name = _qualified(resolved_catalog, "workspace_claims", schema=schema)
    current_table_name = _qualified(
        resolved_catalog, "workspace_claims_current", schema=schema,
    )
    normalized = [claim.normalized() for claim in claims]
    rows = [_claim_to_row(claim) for claim in normalized]

    if _is_dry_run():
        target = _resolve_dry_run_log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "table_name": table_name,
                    "current_table_name": current_table_name,
                    "claims_emitted": len(rows),
                    "claims": rows,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return EmitClaimsResult(
            table_name=table_name,
            claims_emitted=len(rows),
            dry_run=True,
            current_table_name=current_table_name,
            dry_run_log_path=str(target),
        )

    if rows:
        statements = [
            *_render_insert_statements(table_name=table_name, rows=rows),
            *_render_merge_statements(table_name=current_table_name, rows=rows),
        ]
        _execute_statements(statements)
    return EmitClaimsResult(
        table_name=table_name,
        claims_emitted=len(rows),
        dry_run=False,
        current_table_name=current_table_name,
    )


def read_claims_from_dry_run_log(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Read claims emitted by the dry-run writer."""

    target = Path(path) if path else _resolve_dry_run_log_path()
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    claims = payload.get("claims", [])
    return [dict(row) for row in claims if isinstance(row, Mapping)]


def _stable_claim_id(claim: WorkspaceClaim) -> str:
    payload = {
        "workspace_profile_id": claim.workspace_profile_id,
        "workspace_id": claim.workspace_id or "",
        "subject": claim.subject,
        "subject_kind": claim.subject_kind,
        "predicate": claim.predicate,
        "object_ref": claim.object_ref or "",
        "value_json": claim.value_json,
        "source_skill_id": claim.source_skill_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"wkg:{digest}"


def _claim_to_row(claim: WorkspaceClaim) -> dict[str, Any]:
    return dataclasses.asdict(claim)


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "").lower() in ("1", "true", "yes")


def _resolve_catalog() -> str:
    return os.environ.get("BV_CATALOG", "brickvision")


def _resolve_schema() -> str:
    return os.environ.get("BV_SCHEMA", "brickvision")


def _resolve_warehouse_id() -> str:
    warehouse_id = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID")
        or os.environ.get("BV_WAREHOUSE_ID")
        or ""
    ).strip()
    if not warehouse_id:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is required for Workspace KG writes")
    return warehouse_id


def _resolve_dry_run_log_path() -> Path:
    raw = os.environ.get("BV_DRY_RUN_KG_CLAIMS_LOG", "").strip()
    if raw:
        return Path(raw)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "tests" / "fixtures" / "kg" / "last_emit_claims.json"


def _qualified(catalog: str, table: str, *, schema: str | None = None) -> str:
    return f"{catalog}.{schema or _resolve_schema()}.{table}"


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_value_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return _sql_string_literal(str(value))


def _render_insert_statements(
    *, table_name: str, rows: Sequence[dict[str, Any]],
) -> Sequence[str]:
    if not rows:
        return ()
    columns = list(rows[0].keys())
    column_list = "(" + ", ".join(f"`{c}`" for c in columns) + ")"
    statements: list[str] = []
    batch_size = 100
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        values_parts = []
        for row in chunk:
            values_parts.append(
                "(" + ", ".join(_sql_value_literal(row.get(col)) for col in columns) + ")"
            )
        statements.append(
            f"INSERT INTO {table_name} {column_list} VALUES " + ", ".join(values_parts)
        )
    return statements


def _render_merge_statements(
    *, table_name: str, rows: Sequence[dict[str, Any]],
) -> Sequence[str]:
    if not rows:
        return ()
    columns = list(rows[0].keys())
    assignments = ", ".join(f"target.`{col}` = source.`{col}`" for col in columns)
    insert_columns = ", ".join(f"`{col}`" for col in columns)
    insert_values = ", ".join(f"source.`{col}`" for col in columns)
    statements: list[str] = []
    batch_size = 100
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        selects = []
        for row in chunk:
            selects.append(
                "SELECT "
                + ", ".join(
                    f"{_sql_value_literal(row.get(col))} AS `{col}`" for col in columns
                )
            )
        source_sql = " UNION ALL ".join(selects)
        statements.append(
            f"MERGE INTO {table_name} AS target "
            f"USING ({source_sql}) AS source "
            "ON target.`claim_id` = source.`claim_id` "
            f"WHEN MATCHED THEN UPDATE SET {assignments} "
            f"WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})"
        )
    return statements


def _execute_statements(statements: Sequence[str]) -> None:
    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

    client = WorkspaceClient()
    warehouse_id = _resolve_warehouse_id()
    for statement in statements:
        response = client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=warehouse_id,
            wait_timeout="50s",
        )
        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            err = response.status.error if response.status else None
            msg = err.message if err else "(no error message)"
            raise RuntimeError(
                f"Statement Execution returned state={state}; error={msg}"
            )


__all__ = [
    "EmitClaimsResult",
    "WorkspaceClaim",
    "emit_claims",
    "read_claims_from_dry_run_log",
]
