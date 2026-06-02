"""Sample live BrickVision evaluation events into a MLflow GenAI dataset."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

import evaluation_lib as eval_lib


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_env_overrides(args)
    dataset_name = eval_lib.render_env(args.dataset_name) if args.dataset_name else _default_dataset_name(args)
    records = _sample_records(
        workflow=args.workflow,
        event_kind=args.event_kind,
        hours=args.hours,
        limit=args.limit,
        require_trace=not args.include_untraced,
    )
    payload = {
        "status": "ok",
        "dataset_name": dataset_name,
        "workflow": args.workflow,
        "event_kind": args.event_kind,
        "window_hours": args.hours,
        "sample_size": len(records),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(payload | {"records": records[: args.preview_limit]}, indent=2, sort_keys=True))
        return 0
    if not records:
        print(json.dumps(payload | {"registered": False}, indent=2, sort_keys=True))
        return 0

    experiment_id = os.environ.get("BV_MLFLOW_EVALUATION_EXPERIMENT_ID", "").strip()
    if not experiment_id:
        raise ValueError("BV_MLFLOW_EVALUATION_EXPERIMENT_ID is required for live trace sampling")
    dataset = eval_lib.get_or_create_dataset(
        name=dataset_name,
        experiment_id=experiment_id,
        tags={
            "brickvision_dataset_source": "live_trace_sample",
            "workflow": args.workflow,
            "event_kind": args.event_kind,
            "window_hours": str(args.hours),
        },
    )
    dataset = dataset.merge_records(eval_lib.mlflow_records(records))
    dataset_id = str(getattr(dataset, "dataset_id", "") or dataset_name)
    eval_lib.register_dataset(
        dataset_id=dataset_id,
        name=dataset_name,
        workflow=args.workflow,
        uc_table_name=dataset_name,
        experiment_id=experiment_id,
        description=(
            f"Live trace sample for {args.workflow}/{args.event_kind}; "
            f"last {args.hours} hours, limit {args.limit}."
        ),
        quality_gates={"minimum_live_sample_size": min(args.limit, max(1, len(records)))},
        tags={
            "brickvision_dataset_source": "live_trace_sample",
            "workflow": args.workflow,
            "event_kind": args.event_kind,
        },
        source_kinds=["trace"],
    )
    print(json.dumps(payload | {"dataset_id": dataset_id, "registered": True}, indent=2, sort_keys=True))
    return 0


def _sample_records(
    *,
    workflow: str,
    event_kind: str,
    hours: int,
    limit: int,
    require_trace: bool,
) -> list[dict[str, Any]]:
    since_ms = int(time.time() * 1000) - max(1, int(hours)) * 60 * 60 * 1000
    trace_clause = "AND mlflow_trace_id IS NOT NULL AND mlflow_trace_id <> ''" if require_trace else ""
    rows = eval_lib.query_sql(
        f"""
        SELECT
          event_id,
          subject_id,
          user_id,
          status,
          mlflow_trace_id,
          mlflow_run_id,
          metrics_json,
          inputs_json,
          outputs_json,
          evidence_json,
          reason_codes_json,
          created_at_ms
        FROM {eval_lib.qualified_uc_name("evaluation_events")}
        WHERE workflow = {eval_lib.sql_string_literal(workflow)}
          AND event_kind = {eval_lib.sql_string_literal(event_kind)}
          AND created_at_ms >= {since_ms}
          {trace_clause}
        ORDER BY created_at_ms DESC
        LIMIT {max(1, min(int(limit), 5000))}
        """
    )
    return [_record_from_row(row=row, workflow=workflow, event_kind=event_kind) for row in rows]


def _record_from_row(*, row: list[Any], workflow: str, event_kind: str) -> dict[str, Any]:
    event_id = str(row[0])
    inputs = _decode_json_object(row[7])
    outputs = _decode_json_object(row[8])
    evidence = _decode_json_list(row[9])
    reason_codes = _decode_json_list(row[10])
    trace_id = str(row[4] or "")
    status = str(row[3] or "")
    return {
        "dataset_record_id": f"live-{event_id}",
        "inputs": inputs,
        "outputs": outputs,
        "expectations": {
            "guidelines": [
                "Response must be relevant to the user request.",
                "Response must not make unsupported Databricks capability claims.",
                "If evidence is insufficient, response should say so rather than inventing details.",
            ]
        },
        "source": {
            "trace": {
                "mlflow_trace_id": trace_id,
                "mlflow_run_id": str(row[5] or ""),
                "event_id": event_id,
                "subject_id": str(row[1] or ""),
                "created_at_ms": int(row[11] or 0),
            }
        },
        "tags": {
            "workflow": workflow,
            "event_kind": event_kind,
            "dataset_source": "live_trace_sample",
            "runtime_status": status,
            "user_id": str(row[2] or ""),
            "reason_codes": ",".join(str(item) for item in reason_codes),
            "evidence_count": str(len(evidence)),
        },
    }


def _default_dataset_name(args: argparse.Namespace) -> str:
    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    day = time.strftime("%Y%m%d", time.gmtime())
    safe_workflow = str(args.workflow).replace("-", "_")
    safe_event = str(args.event_kind).replace("-", "_")
    return f"{catalog}.{schema}.bv_eval_live_{safe_workflow}_{safe_event}_{day}"


def _decode_json_object(value: Any) -> dict[str, Any]:
    decoded = _decode_maybe_json(value)
    return decoded if isinstance(decoded, dict) else {}


def _decode_json_list(value: Any) -> list[Any]:
    decoded = _decode_maybe_json(value)
    return decoded if isinstance(decoded, list) else []


def _decode_maybe_json(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="hipporag2_retrieval")
    parser.add_argument("--event-kind", default="rag_answer")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--preview-limit", type=int, default=5)
    parser.add_argument("--dataset-name", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-untraced", action="store_true")
    parser.add_argument("--catalog", default="")
    parser.add_argument("--schema", default="")
    parser.add_argument("--warehouse-id", default="")
    parser.add_argument("--mlflow-experiment-id", default="")
    return parser.parse_args(argv)


def _apply_env_overrides(args: argparse.Namespace) -> None:
    if args.catalog:
        os.environ["BV_CATALOG"] = str(args.catalog)
    if args.schema:
        os.environ["BV_SCHEMA"] = str(args.schema)
    if args.warehouse_id:
        os.environ["DATABRICKS_WAREHOUSE_ID"] = str(args.warehouse_id)
    if args.mlflow_experiment_id:
        os.environ["BV_MLFLOW_EVALUATION_EXPERIMENT_ID"] = str(args.mlflow_experiment_id)


if __name__ == "__main__":
    raise SystemExit(main())
