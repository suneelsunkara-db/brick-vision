"""Databricks Jobs driver for BrickVision PySpark Delta transforms."""

from __future__ import annotations

import base64
import json
import sys
from typing import Any


def main(argv: list[str] | None = None) -> int:
    payload = _decode_payload(argv if argv is not None else sys.argv[1:])
    spark = _spark()
    namespace: dict[str, Any] = {}
    exec(str(payload["transform_code"]), namespace)
    transform = namespace.get("transform")
    if not callable(transform):
        raise RuntimeError("Generated PySpark code must define transform(spark, inputs).")
    result = transform(spark, _input_frames(spark, list(payload["input_uris"])))
    if result is None:
        raise RuntimeError("Generated PySpark transform returned None.")
    output_uri = str(payload["output_uri"])
    result.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(output_uri)
    return 0


def _decode_payload(args: list[str]) -> dict[str, Any]:
    if len(args) != 1:
        raise RuntimeError("Expected one base64-encoded JSON payload argument.")
    payload = json.loads(base64.b64decode(args[0]).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("PySpark transform payload must decode to an object.")
    return payload


def _spark() -> Any:
    from pyspark.sql import SparkSession  # type: ignore[import-not-found]

    return SparkSession.builder.getOrCreate()


def _input_frames(spark: Any, input_uris: list[str]) -> dict[str, Any]:
    frames: dict[str, Any] = {}
    seen_aliases: set[str] = set()
    for uri in input_uris:
        frame = spark.table(uri)
        frames[uri] = frame
        alias = uri.replace("`", "").split(".")[-1]
        if alias and alias not in seen_aliases:
            frames[alias] = frame
            seen_aliases.add(alias)
    return frames


if __name__ == "__main__":  # pragma: no cover - Databricks task entrypoint
    main()
