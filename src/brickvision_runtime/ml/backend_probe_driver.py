"""Databricks runtime probe for BrickVision ML backend selection.

This driver is intentionally non-training. It runs tiny import/API checks on
the target Jobs runtime and writes one evidence row for skill selection.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    spark = _spark()
    evidence = _probe_runtime()
    row = {
        "probe_id": args.probe_id,
        "runtime_surface": args.runtime_surface,
        "databricks_automl_available": evidence["databricks_automl_available"],
        "spark_ml_allowed": evidence["spark_ml_allowed"],
        "mlflow_uc_registry_available": evidence["mlflow_uc_registry_available"],
        "mosaic_ai_available": evidence["mosaic_ai_available"],
        "substrate_json": json.dumps(evidence["substrate"], sort_keys=True),
        "errors_json": json.dumps(evidence["errors"], sort_keys=True),
        "created_at_ms": int(time.time() * 1000),
    }
    spark.createDataFrame([row]).write.format("delta").mode("append").saveAsTable(args.probe_output_table)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Databricks ML backend availability.")
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--runtime-surface", required=True)
    parser.add_argument("--probe-output-table", required=True)
    return parser.parse_args(argv)


def _spark() -> Any:
    from pyspark.sql import SparkSession  # type: ignore[import-not-found]

    return SparkSession.builder.getOrCreate()


def _probe_runtime() -> dict[str, Any]:
    errors: dict[str, str] = {}
    databricks_automl_available = _probe_import("databricks.automl", errors, "databricks_automl")
    mlflow_uc_registry_available = _probe_mlflow_uc(errors)
    mlflow_pyfunc_available = _probe_import("mlflow.pyfunc", errors, "mlflow_pyfunc")
    spark_ml_allowed = _probe_spark_ml(errors)
    mosaic_ai_available = _probe_import("databricks.sdk", errors, "mosaic_ai_sdk_surface")
    python_imports = _probe_python_ml_imports(errors)
    data_movement = _probe_data_movement(errors)
    return {
        "databricks_automl_available": databricks_automl_available,
        "spark_ml_allowed": spark_ml_allowed,
        "mlflow_uc_registry_available": mlflow_uc_registry_available,
        "mosaic_ai_available": mosaic_ai_available,
        "substrate": {
            "python_imports": python_imports,
            "data_movement": data_movement,
            "mlflow_primitives": {
                "pyfunc_available": mlflow_pyfunc_available,
                "uc_registry_uri_set": mlflow_uc_registry_available,
            },
        },
        "errors": errors,
    }


def _probe_import(module_name: str, errors: dict[str, str], key: str) -> bool:
    try:
        __import__(module_name)
    except Exception as exc:  # pragma: no cover - Databricks runtime only
        errors[key] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def _probe_mlflow_uc(errors: dict[str, str]) -> bool:
    try:
        import mlflow  # type: ignore[import-not-found]

        mlflow.set_registry_uri("databricks-uc")
    except Exception as exc:  # pragma: no cover - Databricks runtime only
        errors["mlflow_uc_registry"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def _probe_spark_ml(errors: dict[str, str]) -> bool:
    try:
        from pyspark.ml.feature import VectorAssembler  # type: ignore[import-not-found]

        VectorAssembler(inputCols=["x"], outputCol="features")
    except Exception as exc:  # pragma: no cover - Databricks runtime only
        errors["spark_ml"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def _probe_python_ml_imports(errors: dict[str, str]) -> dict[str, bool]:
    modules = {
        "numpy": "numpy",
        "pandas": "pandas",
        "pyarrow": "pyarrow",
        "cloudpickle": "cloudpickle",
        "sklearn": "sklearn",
        "xgboost": "xgboost",
        "lightgbm": "lightgbm",
        "statsmodels": "statsmodels",
        "prophet": "prophet",
    }
    return {
        name: _probe_import(module, errors, f"python_import:{name}")
        for name, module in modules.items()
    }


def _probe_data_movement(errors: dict[str, str]) -> dict[str, bool]:
    result = {
        "spark_dataframe_available": False,
        "arrow_conf_readable": False,
        "to_pandas_available": False,
        "arrow_conf_enabled": False,
    }
    try:
        spark = _spark()
        result["spark_dataframe_available"] = True
        import pandas as pd  # type: ignore[import-not-found]

        rows = spark.createDataFrame([(1, 2.0)], ["id", "value"]).collect()
        pdf = pd.DataFrame([row.asDict(recursive=True) for row in rows])
        result["to_pandas_available"] = len(pdf) == 1
    except Exception as exc:  # pragma: no cover - Databricks runtime only
        errors["data_movement"] = f"{type(exc).__name__}: {exc}"
    return result


if __name__ == "__main__":  # pragma: no cover - Databricks task entrypoint
    main()
