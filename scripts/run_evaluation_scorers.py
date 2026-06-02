"""Run the first BrickVision evaluation scorer pass over manifest-backed eval sets.

This runner is intentionally generic: it does not hardcode smoke queries or
workflow-specific answers. It validates MLflow-style records, computes dataset
coverage metrics, applies manifest-declared quality gates, and can persist one
``scorer_run`` evaluation event per dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import evaluation_lib as eval_lib


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_env_overrides(args)
    manifest_path = Path(args.manifest)
    manifest = eval_lib.load_manifest(manifest_path)
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("manifest must contain a datasets array")

    summaries = [
        _score_dataset(
            spec,
            base_dir=manifest_path.parent,
            dry_run=args.dry_run,
            mlflow_genai_evaluate=args.mlflow_genai_evaluate,
        )
        for spec in datasets
        if isinstance(spec, dict)
    ]
    failed = [item for item in summaries if item.get("status") == "failed"]
    status = "failed" if failed else "ok"
    print(json.dumps({"status": status, "scorer_runs": summaries}, indent=2, sort_keys=True))
    return 1 if failed else 0


def _score_dataset(
    spec: dict[str, Any],
    *,
    base_dir: Path,
    dry_run: bool,
    mlflow_genai_evaluate: bool,
) -> dict[str, Any]:
    name = eval_lib.render_env(eval_lib.required_string(spec, "name"))
    workflow = eval_lib.required_string(spec, "workflow")
    record_path = base_dir / eval_lib.required_string(spec, "records")
    records = eval_lib.load_records(record_path)
    quality_gates = spec.get("quality_gates") if isinstance(spec.get("quality_gates"), dict) else {}
    metrics = _dataset_metrics(records)
    if not dry_run:
        metrics |= _runtime_metrics(workflow=workflow, records=records)
    mlflow_result: dict[str, Any] = {"status": "skipped"}
    if mlflow_genai_evaluate and not dry_run:
        mlflow_result = _maybe_run_mlflow_genai_evaluate(
            dataset_name=name,
            workflow=workflow,
            records=records,
        )
        metrics["mlflow_genai_evaluate_status"] = mlflow_result["status"]
        metrics["mlflow_genai_scored_record_count"] = int(
            mlflow_result.get("scored_record_count") or 0
        )
    if workflow == "hipporag2_retrieval":
        metrics["runtime_grounded_answer_rate"] = _min_metric(
            metrics.get("runtime_expected_context_hit_rate"),
            metrics.get("runtime_answer_completion_rate"),
        )
    status, reason_codes = _apply_quality_gates(metrics=metrics, quality_gates=quality_gates)
    if mlflow_result.get("status") == "failed":
        reason_codes.append("EVAL_MLFLOW_GENAI_EVALUATE_FAILED")
        status = "failed"
    scorer_results = _scorer_results(
        workflow=workflow,
        metrics=metrics,
        quality_gates=quality_gates,
        reason_codes=reason_codes,
    )
    summary = {
        "dataset_name": name,
        "workflow": workflow,
        "status": status,
        "metrics": metrics,
        "quality_gates": quality_gates,
        "scorer_results": scorer_results,
        "reason_codes": reason_codes,
        "dry_run": dry_run,
        "mlflow_genai_evaluate": mlflow_result,
    }
    if not dry_run:
        _persist_scorer_event(
            dataset_name=name,
            workflow=workflow,
            status=status,
            metrics=metrics,
            quality_gates=quality_gates,
            scorer_results=scorer_results,
            reason_codes=reason_codes,
            mlflow_run_id=str(mlflow_result.get("run_id") or ""),
            mlflow_result=mlflow_result,
        )
        summary["persisted"] = True
    return summary


def _dataset_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    record_count = len(records)
    with_expectations = [record for record in records if isinstance(record.get("expectations"), dict)]
    with_source = [record for record in records if isinstance(record.get("source"), dict)]
    with_expected_context = [
        record
        for record in records
        if isinstance(record.get("expectations"), dict)
        and record["expectations"].get("expected_retrieved_context")
    ]
    with_guidelines = [
        record
        for record in records
        if isinstance(record.get("expectations"), dict)
        and record["expectations"].get("guidelines")
    ]
    return {
        "record_count": record_count,
        "expectation_coverage": _ratio(len(with_expectations), record_count),
        "source_coverage": _ratio(len(with_source), record_count),
        "expected_context_coverage": _ratio(len(with_expected_context), record_count),
        "guideline_coverage": _ratio(len(with_guidelines), record_count),
    }


def _runtime_metrics(*, workflow: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        if workflow == "capability_graph":
            return _retrieval_runtime_metrics(records=records, event_kind="rag_search")
        if workflow == "hipporag2_retrieval":
            return _retrieval_runtime_metrics(records=records, event_kind="rag_answer")
    except Exception as exc:  # noqa: BLE001
        return {
            "runtime_scored_record_count": 0,
            "runtime_event_query_error": str(exc)[:500],
        }
    return {}


def _retrieval_runtime_metrics(*, records: list[dict[str, Any]], event_kind: str) -> dict[str, Any]:
    subject_ids = [_subject_id_for_record(record) for record in records]
    events = _load_latest_events(event_kind=event_kind, subject_ids=subject_ids)
    scored = 0
    expected_context_hits = 0
    top_1_context_hits = 0
    answer_completed = 0
    for record in records:
        event = events.get(_subject_id_for_record(record))
        if not event:
            continue
        scored += 1
        if _expected_context_present(record=record, event=event):
            expected_context_hits += 1
        if _expected_context_present(record=record, event=event, rank=1):
            top_1_context_hits += 1
        if _answer_completed(event):
            answer_completed += 1
    return {
        "runtime_scored_record_count": scored,
        "runtime_expected_context_hit_rate": _ratio(expected_context_hits, scored),
        "runtime_top_1_hit_rate": _ratio(top_1_context_hits, scored),
        "runtime_answer_completion_rate": _ratio(answer_completed, scored),
    }


def _apply_quality_gates(
    *,
    metrics: dict[str, Any],
    quality_gates: dict[str, Any],
) -> tuple[str, list[str]]:
    reason_codes: list[str] = []
    required_record_count = int(quality_gates.get("required_record_count") or 1)
    if int(metrics["record_count"]) < required_record_count:
        reason_codes.append("EVAL_DATASET_RECORD_COUNT_BELOW_FLOOR")
    if float(metrics["expectation_coverage"]) < 1.0:
        reason_codes.append("EVAL_DATASET_EXPECTATIONS_INCOMPLETE")
    if float(metrics["source_coverage"]) < 1.0:
        reason_codes.append("EVAL_DATASET_SOURCE_INCOMPLETE")
    runtime_scored = metrics.get("runtime_scored_record_count")
    if runtime_scored is not None and int(runtime_scored) == 0:
        reason_codes.append("EVAL_RUNTIME_EVENTS_MISSING")
    if quality_gates.get("top_1_hit_rate_floor") is not None and metrics.get("runtime_top_1_hit_rate") is not None:
        if float(metrics["runtime_top_1_hit_rate"]) < float(quality_gates["top_1_hit_rate_floor"]):
            reason_codes.append("EVAL_TOP_1_HIT_RATE_BELOW_FLOOR")
    if quality_gates.get("document_recall_floor") is not None and metrics.get("runtime_expected_context_hit_rate") is not None:
        if float(metrics["runtime_expected_context_hit_rate"]) < float(quality_gates["document_recall_floor"]):
            reason_codes.append("EVAL_DOCUMENT_RECALL_BELOW_FLOOR")
    if quality_gates.get("grounded_answer_rate_floor") is not None and metrics.get("runtime_grounded_answer_rate") is not None:
        if float(metrics["runtime_grounded_answer_rate"]) < float(quality_gates["grounded_answer_rate_floor"]):
            reason_codes.append("EVAL_GROUNDED_ANSWER_RATE_BELOW_FLOOR")
    return ("passed" if not reason_codes else "failed", reason_codes)


def _scorer_results(
    *,
    workflow: str,
    metrics: dict[str, Any],
    quality_gates: dict[str, Any],
    reason_codes: list[str],
) -> list[dict[str, Any]]:
    if workflow == "capability_graph":
        return [
            _scorer_result(
                name="capability_graph_top_1_retrieval",
                label="Top-1 retrieval accuracy",
                value=metrics.get("runtime_top_1_hit_rate"),
                threshold=quality_gates.get("top_1_hit_rate_floor"),
                business_label="Can BrickVision route a user request to the right Databricks capability?",
                reason_codes=reason_codes,
            ),
            _scorer_result(
                name="capability_graph_expected_context_recall",
                label="Expected context recall",
                value=metrics.get("runtime_expected_context_hit_rate"),
                threshold=quality_gates.get("document_recall_floor"),
                business_label="Does the retrieved evidence contain the curated gold capability?",
                reason_codes=reason_codes,
            ),
        ]
    if workflow == "hipporag2_retrieval":
        grounded_rate = _min_metric(
            metrics.get("runtime_expected_context_hit_rate"),
            metrics.get("runtime_answer_completion_rate"),
        )
        metrics["runtime_grounded_answer_rate"] = grounded_rate
        return [
            _scorer_result(
                name="hipporag2_document_recall",
                label="Document recall",
                value=metrics.get("runtime_expected_context_hit_rate"),
                threshold=quality_gates.get("document_recall_floor"),
                business_label="Did the answer use the source document the business expects?",
                reason_codes=reason_codes,
            ),
            _scorer_result(
                name="hipporag2_grounded_answer_rate",
                label="Grounded answer rate",
                value=grounded_rate,
                threshold=quality_gates.get("grounded_answer_rate_floor"),
                business_label="Can users trust the answer because it was produced with supporting evidence?",
                reason_codes=reason_codes,
            ),
        ]
    return [
        _scorer_result(
            name=f"{workflow}_dataset_readiness",
            label="Dataset readiness",
            value=metrics.get("expectation_coverage"),
            threshold=1.0,
            business_label="Is this workflow ready to be scored consistently?",
            reason_codes=reason_codes,
        )
    ]


def _scorer_result(
    *,
    name: str,
    label: str,
    value: Any,
    threshold: Any,
    business_label: str,
    reason_codes: list[str],
) -> dict[str, Any]:
    numeric_value = _optional_float(value)
    numeric_threshold = _optional_float(threshold)
    status = "not_run"
    if numeric_value is not None and numeric_threshold is not None:
        status = "passed" if numeric_value >= numeric_threshold else "failed"
    elif numeric_value is not None:
        status = "observed"
    if reason_codes and status == "passed":
        status = "failed"
    return {
        "name": name,
        "label": label,
        "value": numeric_value,
        "threshold": numeric_threshold,
        "status": status,
        "business_label": business_label,
    }


def _persist_scorer_event(
    *,
    dataset_name: str,
    workflow: str,
    status: str,
    metrics: dict[str, Any],
    quality_gates: dict[str, Any],
    scorer_results: list[dict[str, Any]],
    reason_codes: list[str],
    mlflow_run_id: str,
    mlflow_result: dict[str, Any],
) -> None:
    eval_lib.execute_sql(
        f"""
        CREATE TABLE IF NOT EXISTS {eval_lib.qualified_uc_name("evaluation_events")} (
          event_id STRING NOT NULL,
          event_kind STRING NOT NULL,
          workflow STRING NOT NULL,
          status STRING NOT NULL,
          subject_id STRING NOT NULL,
          user_id STRING,
          mlflow_run_id STRING,
          mlflow_trace_id STRING,
          mlflow_dataset_name STRING,
          metrics_json STRING,
          inputs_json STRING,
          outputs_json STRING,
          expectations_json STRING,
          evidence_json STRING,
          reason_codes_json STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'evaluation_events')
        """
    )
    now_ms = int(time.time() * 1000)
    event_id = _event_id(dataset_name=dataset_name, workflow=workflow, created_at_ms=now_ms)
    eval_lib.execute_sql(
        f"""
        INSERT INTO {eval_lib.qualified_uc_name("evaluation_events")} (
          event_id,
          event_kind,
          workflow,
          status,
          subject_id,
          user_id,
          mlflow_run_id,
          mlflow_trace_id,
          mlflow_dataset_name,
          metrics_json,
          inputs_json,
          outputs_json,
          expectations_json,
          evidence_json,
          reason_codes_json,
          created_at_ms
        )
        VALUES (
          {eval_lib.sql_string_literal(event_id)},
          'scorer_run',
          {eval_lib.sql_string_literal(workflow)},
          {eval_lib.sql_string_literal(status)},
          {eval_lib.sql_string_literal(dataset_name)},
          {eval_lib.sql_string_literal(os.environ.get("USER", "unknown"))},
          {eval_lib.sql_string_literal(mlflow_run_id)},
          '',
          {eval_lib.sql_string_literal(dataset_name)},
          {eval_lib.sql_string_literal(json.dumps(metrics, sort_keys=True))},
          {eval_lib.sql_string_literal(json.dumps({"dataset_name": dataset_name, "mlflow_genai_evaluate": mlflow_result}, sort_keys=True))},
          {eval_lib.sql_string_literal(json.dumps({"quality_gates": quality_gates, "scorer_results": scorer_results}, sort_keys=True))},
          '{{}}',
          {eval_lib.sql_string_literal(json.dumps(scorer_results, sort_keys=True))},
          {eval_lib.sql_string_literal(json.dumps(reason_codes, sort_keys=True))},
          {now_ms}
        )
        """
    )


def _maybe_run_mlflow_genai_evaluate(
    *,
    dataset_name: str,
    workflow: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    event_kind = _event_kind_for_workflow(workflow)
    if not event_kind:
        return {"status": "skipped", "reason": "workflow_has_no_runtime_event_mapping"}

    events = _load_latest_events(
        event_kind=event_kind,
        subject_ids=[_subject_id_for_record(record) for record in records],
    )
    rows = _mlflow_eval_rows(records=records, events=events)
    if not rows:
        return {"status": "skipped", "reason": "no_matching_runtime_events"}

    try:
        import mlflow
    except ModuleNotFoundError as exc:
        return {"status": "skipped", "reason": f"mlflow_unavailable:{exc.name}"}

    try:
        eval_lib.configure_mlflow_tracking(mlflow)
        experiment_id = os.environ.get("BV_MLFLOW_EVALUATION_EXPERIMENT_ID", "").strip()
        if experiment_id:
            mlflow.set_experiment(experiment_id=experiment_id)
        scorers = _mlflow_genai_scorers(workflow=workflow)
        if not scorers:
            return {
                "status": "skipped",
                "reason": "mlflow_genai_scorers_unavailable",
                "scored_record_count": len(rows),
            }
        result = mlflow.genai.evaluate(
            data=rows,
            scorers=scorers,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "reason": "mlflow_genai_evaluate_failed",
            "error": str(exc)[:1000],
            "scored_record_count": len(rows),
        }

    run_id = str(getattr(result, "run_id", "") or "")
    return {
        "status": "passed",
        "run_id": run_id,
        "scored_record_count": len(rows),
    }


def _event_kind_for_workflow(workflow: str) -> str:
    if workflow == "capability_graph":
        return "rag_search"
    if workflow == "hipporag2_retrieval":
        return "rag_answer"
    return ""


def _mlflow_eval_rows(
    *,
    records: list[dict[str, Any]],
    events: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        subject_id = _subject_id_for_record(record)
        event = events.get(subject_id)
        if not event:
            continue
        row: dict[str, Any] = {
            "inputs": record.get("inputs") if isinstance(record.get("inputs"), dict) else {},
            "outputs": event.get("outputs") if isinstance(event.get("outputs"), dict) else {},
            "expectations": record.get("expectations")
            if isinstance(record.get("expectations"), dict)
            else {},
        }
        evidence = event.get("evidence")
        if evidence:
            row["source"] = {"runtime_evidence": evidence}
        trace_id = str(event.get("mlflow_trace_id") or "")
        if trace_id:
            row["source"] = {**eval_lib.object_or_empty(row.get("source")), "trace": {"mlflow_trace_id": trace_id}}
        rows.append(row)
    return rows


def _mlflow_genai_scorers(*, workflow: str) -> list[Any]:
    try:
        from mlflow.genai import scorers
    except Exception:  # noqa: BLE001
        return []

    if workflow != "hipporag2_retrieval":
        return []

    scorer_names = ["Safety", "Correctness", "RelevanceToQuery"]

    loaded: list[Any] = []
    for name in scorer_names:
        factory = getattr(scorers, name, None)
        if factory is None:
            continue
        try:
            loaded.append(factory())
        except TypeError:
            loaded.append(factory)
    return loaded


def _load_latest_events(*, event_kind: str, subject_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not subject_ids:
        return {}
    quoted_subjects = ", ".join(eval_lib.sql_string_literal(subject_id) for subject_id in subject_ids)
    rows = eval_lib.query_sql(
        f"""
        SELECT subject_id, inputs_json, outputs_json, evidence_json, mlflow_run_id, mlflow_trace_id
        FROM (
          SELECT
            subject_id,
            inputs_json,
            outputs_json,
            evidence_json,
            mlflow_run_id,
            mlflow_trace_id,
            ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY created_at_ms DESC) AS rn
          FROM {eval_lib.qualified_uc_name("evaluation_events")}
          WHERE event_kind = {eval_lib.sql_string_literal(event_kind)}
            AND subject_id IN ({quoted_subjects})
        )
        WHERE rn = 1
        """
    )
    return {
        str(row[0]): {
            "inputs": _decode_json(row[1]),
            "outputs": _decode_json(row[2]),
            "evidence": _decode_json(row[3]),
            "mlflow_run_id": str(row[4] or ""),
            "mlflow_trace_id": str(row[5] or ""),
        }
        for row in rows
    }


def _expected_context_present(
    *, record: dict[str, Any], event: dict[str, Any], rank: int | None = None
) -> bool:
    expectations = record.get("expectations")
    if not isinstance(expectations, dict):
        return False
    expected_context = expectations.get("expected_retrieved_context")
    if not isinstance(expected_context, list) or not expected_context:
        return True
    haystack_source: Any = event
    if rank is not None:
        evidence = event.get("evidence")
        haystack_source = evidence[:rank] if isinstance(evidence, list) else []
    haystack = json.dumps(haystack_source, sort_keys=True)
    for item in expected_context:
        if not isinstance(item, dict):
            continue
        expected_entity = str(item.get("entity_id") or "")
        expected_doc = str(item.get("doc_uri") or "")
        expected_content = str(item.get("content_contains") or "")
        if expected_entity and expected_entity in haystack:
            return True
        if expected_doc and expected_doc in haystack:
            return True
        if expected_content and expected_content in haystack:
            return True
    return False


def _answer_completed(event: dict[str, Any]) -> bool:
    outputs = event.get("outputs")
    if not isinstance(outputs, dict):
        return False
    answer = str(outputs.get("answer") or "")
    return bool(answer.strip()) and not answer.startswith("Generation failed:")


def _subject_id_for_record(record: dict[str, Any]) -> str:
    inputs = record.get("inputs") if isinstance(record.get("inputs"), dict) else {}
    query = str(inputs.get("query") or inputs.get("question") or "")
    normalized = " ".join(query.strip().lower().split())
    if not normalized:
        return "query_empty"
    return "query_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _decode_json(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _min_metric(*values: Any) -> float | None:
    parsed = [_optional_float(value) for value in values]
    numeric = [value for value in parsed if value is not None]
    if not numeric:
        return None
    return min(numeric)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_id(*, dataset_name: str, workflow: str, created_at_ms: int) -> str:
    raw = "|".join((dataset_name, workflow, str(created_at_ms)))
    return "evalscore_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


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
        help="Validate records and compute metrics without writing scorer events.",
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
    parser.add_argument(
        "--mlflow-genai-evaluate",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("BV_EVALUATION_USE_MLFLOW_GENAI", default=False),
        help=(
            "Also run an opt-in MLflow GenAI Agent Evaluation pass over runtime-matched "
            "records and persist the MLflow run id on scorer_run events."
        ),
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


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code:
        raise SystemExit(_exit_code)
