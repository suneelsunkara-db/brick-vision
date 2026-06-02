"""Sync BrickVision evaluation sets into MLflow GenAI evaluation datasets.

Records are loaded from JSONL files, validated against the Databricks MLflow
evaluation dataset contract, merged into MLflow datasets, and registered in the
BrickVision UC read model consumed by the Console Evaluation page.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import evaluation_lib as eval_lib

WORKFLOWS = {
    "capability_graph",
    "hipporag2_retrieval",
    "workspace_context",
    "usecase_lifecycle",
    "skill_execution",
    "platform_cost",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_env_overrides(args)
    manifest_path = Path(args.manifest)
    manifest = eval_lib.load_manifest(manifest_path)
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("manifest must contain a datasets array")

    results: list[dict[str, Any]] = []
    for item in datasets:
        if not isinstance(item, dict):
            raise ValueError("each dataset manifest item must be an object")
        results.append(
            _sync_dataset(
                item,
                base_dir=manifest_path.parent,
                dry_run=args.dry_run,
            )
        )
    print(json.dumps({"status": "ok", "datasets": results}, indent=2, sort_keys=True))
    return 0


def _sync_dataset(
    spec: dict[str, Any],
    *,
    base_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    name = eval_lib.render_env(eval_lib.required_string(spec, "name"))
    workflow = eval_lib.required_string(spec, "workflow")
    if workflow not in WORKFLOWS:
        raise ValueError(f"unsupported workflow {workflow!r}")
    record_path = base_dir / eval_lib.required_string(spec, "records")
    records = eval_lib.load_records(record_path)
    experiment_id = eval_lib.render_env(
        str(spec.get("experiment_id") or os.environ.get(
            "BV_MLFLOW_EVALUATION_EXPERIMENT_ID",
            "",
        ))
    )
    tags = eval_lib.object_or_empty(spec.get("tags"))
    quality_gates = eval_lib.object_or_empty(spec.get("quality_gates"))

    if dry_run:
        return {
            "name": name,
            "workflow": workflow,
            "record_count": len(records),
            "dry_run": True,
        }
    if not experiment_id:
        raise ValueError(
            "BV_MLFLOW_EVALUATION_EXPERIMENT_ID is required for live MLflow GenAI "
            "dataset sync. Create a Databricks MLflow experiment and pass its id."
        )

    dataset = eval_lib.get_or_create_dataset(
        name=name,
        experiment_id=experiment_id,
        tags=tags,
    )
    if records:
        dataset = dataset.merge_records(eval_lib.mlflow_records(records))

    dataset_id = str(getattr(dataset, "dataset_id", "") or eval_lib.stable_dataset_id(name))
    eval_lib.register_dataset(
        dataset_id=dataset_id,
        name=name,
        workflow=workflow,
        uc_table_name=name,
        experiment_id=experiment_id,
        description=str(spec.get("description") or ""),
        quality_gates=quality_gates,
        tags=tags,
        source_kinds=sorted(eval_lib.record_source_kinds(records)),
    )
    return {
        "dataset_id": dataset_id,
        "name": name,
        "workflow": workflow,
        "record_count": len(records),
        "registered": True,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default="config/evaluation/evalsets.json",
        help="Path to the BrickVision evaluation dataset manifest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate manifest and records without creating MLflow datasets.",
    )
    parser.add_argument("--catalog", default="", help="Override BV_CATALOG for this run.")
    parser.add_argument("--schema", default="", help="Override BV_SCHEMA for this run.")
    parser.add_argument(
        "--warehouse-id",
        default="",
        help="Override DATABRICKS_WAREHOUSE_ID for this run.",
    )
    parser.add_argument(
        "--mlflow-experiment-id",
        default="",
        help="Override BV_MLFLOW_EVALUATION_EXPERIMENT_ID for this run.",
    )
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
    _exit_code = main()
    if _exit_code:
        raise SystemExit(_exit_code)
