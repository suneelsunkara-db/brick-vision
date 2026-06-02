"""Contract stub for Databricks ML training artifacts.

BrickVision must not contain a generic ML training engine. ML skills choose a
model family and backend, then bind a real Databricks-native training artifact
such as a generated notebook/script, AutoML artifact, Spark ML pipeline, or
approved project bundle entrypoint.

This file remains only as a guardrail for older bindings that still point at
``brickvision_runtime/ml/training_driver.py``. Use ``skill:ml.training-task-plan``
to bind a concrete training artifact URI instead.
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract = {
        "status": "blocked",
        "reason": "BRICKVISION_RUNTIME_ML_ENGINE_REMOVED",
        "message": (
            "Bind a Databricks-native ML training artifact generated or approved "
            "by the ML skills. BrickVision runtime only submits the selected "
            "Databricks API plan and reads back ModelTrainingRun audit evidence."
        ),
        "artifact_contract": {
            "required_outputs": [
                "ModelTrainingRun audit row appended to audit_table",
                "Unity Catalog registered model version when validation passes",
            ],
            "required_inputs": [
                "rows_uri",
                "model_full_name",
                "feature/label/key parameters as declared by the selected artifact",
                "audit_table",
                "audit_id",
            ],
        },
    }
    if args.contract_json:
        contract["received_contract"] = _json_or_raw(args.contract_json)
    raise SystemExit(json.dumps(contract, sort_keys=True))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-json", default="")
    return parser.parse_args(argv)


def _json_or_raw(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


if __name__ == "__main__":  # pragma: no cover - Databricks task entrypoint
    main()
