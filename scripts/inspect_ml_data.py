"""Inspect candidate ML tables to find target-label-shaped columns.

Run from repo root:
    ./.venv/bin/python -m scripts.inspect_ml_data
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "console-api" / "src"))


def _load_env() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().split(None, 1)[0] if value.strip().startswith(("#",)) else value.strip()
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        os.environ.setdefault(key.strip(), value)


_load_env()

from console_api.databricks_sql import query_sql_statement_rows  # noqa: E402

CATALOG = "partner_demo_catalog"
SCHEMAS_AND_TABLES = {
    "banking_ai_agent": [
        "personalised_insights",
        "behavioural_signals",
        "peer_benchmarks",
        "recurring_expenses",
        "customer_profile",
        "core_transactions",
        "enriched_transactions",
        "monthly_spending_summary",
        "advisory_sessions",
        "advisory_messages",
        "advisory_decisions",
        "advisory_artifacts",
        "user_feedback",
        "usecase_registry",
    ],
    "test_partners": [
        "asean_account_list",
        "all_partner_attach_usecases",
    ],
}

LABEL_TOKENS = (
    "label",
    "target",
    "outcome",
    "churn",
    "fraud",
    "accepted",
    "response",
    "conversion",
    "click",
    "purchase",
    "is_",
    "has_",
    "flag",
    "status",
    "decision",
)


def _column_looks_like_label(column_name: str) -> bool:
    lower = column_name.lower()
    return any(token in lower for token in LABEL_TOKENS)


def main() -> int:
    out = {"catalog": CATALOG, "schemas": {}}
    for schema, tables in SCHEMAS_AND_TABLES.items():
        out["schemas"][schema] = {}
        for table in tables:
            full = f"`{CATALOG}`.`{schema}`.`{table}`"
            try:
                rows = query_sql_statement_rows(
                    f"DESCRIBE TABLE {full}"
                )
            except Exception as exc:  # pragma: no cover
                out["schemas"][schema][table] = {"error": str(exc)}
                continue
            cols = []
            for row in rows:
                # DESCRIBE TABLE: col_name, data_type, comment
                if not row:
                    continue
                name = str(row[0]) if row[0] is not None else ""
                if not name or name.startswith("#") or name == "":
                    continue
                dtype = str(row[1]) if len(row) > 1 and row[1] is not None else ""
                cols.append({
                    "name": name,
                    "type": dtype,
                    "looks_like_label": _column_looks_like_label(name),
                })
            try:
                count_rows = query_sql_statement_rows(
                    f"SELECT COUNT(*) FROM {full}"
                )
                row_count = int(count_rows[0][0]) if count_rows and count_rows[0] else 0
            except Exception as exc:  # pragma: no cover
                row_count = -1
            label_cols = [c["name"] for c in cols if c["looks_like_label"]]
            out["schemas"][schema][table] = {
                "row_count": row_count,
                "n_columns": len(cols),
                "label_candidate_columns": label_cols,
                "all_columns": [c["name"] for c in cols],
            }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
