"""Vector Search out-of-band provisioning pre-flight (N98 + N99).

Per [`docs/18-architecture.md`](../../../../docs/18-architecture.md) §14.1
the install assumes the Vector Search Direct Access (DA) indices
are provisioned **out-of-band** by the partner's platform team
because:

1. VS endpoint creation is a workspace-admin operation that
   BrickVision's install SP is not authorised to perform.
2. The embedding model endpoint binding is a partner-policy
   decision (which model they want, which workspace it lives in).

The install therefore requires a **manifest** declaring every VS
index BrickVision needs, then verifies each one already exists in
the workspace and matches the declared schema. Any drift fails
the install with a typed ``Question`` carrying
``VS_OUT_OF_BAND_PROVISIONING_REQUIRED`` (missing) or
``VS_RESOURCE_SCHEMA_MISMATCH`` (drifted).

This module provides:

- ``VSIndexSpec``      — the manifest entry shape (declared by partner).
- ``VSIndexProbe``     — what the install observed in the workspace
  (typically returned by an SDK adapter).
- ``check_vector_search`` — pure function that compares the two and
  returns typed ``PreFlightFailure``s; used by both the
  ``brickvision install`` runner (N105) and the
  ``vs_resource_schema_conformance`` scorer (N99).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from brickvision.cli.install import PreFlightFailure
from brickvision_runtime.failures import ReasonCode


@dataclasses.dataclass(frozen=True, slots=True)
class VSIndexSpec:
    """Partner-declared VS Direct-Access index spec."""

    name: str
    embedding_model_endpoint: str
    dimension: int
    primary_key: str
    schema: Mapping[str, str]  # column_name -> type literal


@dataclasses.dataclass(frozen=True, slots=True)
class VSIndexProbe:
    """What the install observed for one VS index in the workspace."""

    name: str
    embedding_model_endpoint: str | None
    dimension: int | None
    primary_key: str | None
    schema: Mapping[str, str] | None  # None ⇒ index does not exist


def check_vector_search(
    *,
    declared: Sequence[VSIndexSpec],
    observed: Mapping[str, VSIndexProbe],
) -> list[PreFlightFailure]:
    """N98 — return one failure per declared-but-missing or drifted index.

    Empty list ⇒ pre-flight passes.
    """

    failures: list[PreFlightFailure] = []
    for spec in declared:
        probe = observed.get(spec.name)

        if probe is None or probe.schema is None:
            failures.append(
                PreFlightFailure(
                    reason_code=ReasonCode.VS_OUT_OF_BAND_PROVISIONING_REQUIRED,
                    suggested_next_action=(
                        f"create VS DA index {spec.name!r} via the workspace UI"
                        f" / SDK before re-running brickvision install"
                    ),
                    detail=f"name={spec.name}",
                )
            )
            continue

        drift_detail: list[str] = []
        if probe.embedding_model_endpoint != spec.embedding_model_endpoint:
            drift_detail.append(
                f"embedding_model_endpoint=declared:{spec.embedding_model_endpoint}"
                f" observed:{probe.embedding_model_endpoint}"
            )
        if probe.dimension != spec.dimension:
            drift_detail.append(
                f"dimension=declared:{spec.dimension} observed:{probe.dimension}"
            )
        if probe.primary_key != spec.primary_key:
            drift_detail.append(
                f"primary_key=declared:{spec.primary_key}"
                f" observed:{probe.primary_key}"
            )
        # Schema drift: every declared column must appear with the
        # declared type. Extra observed columns are tolerated.
        for col, typ in spec.schema.items():
            observed_type = probe.schema.get(col)
            if observed_type != typ:
                drift_detail.append(
                    f"schema[{col}]=declared:{typ} observed:{observed_type}"
                )

        if drift_detail:
            failures.append(
                PreFlightFailure(
                    reason_code=ReasonCode.VS_RESOURCE_SCHEMA_MISMATCH,
                    suggested_next_action=(
                        f"reconcile {spec.name!r} schema with the install"
                        f" manifest; details follow"
                    ),
                    detail=f"name={spec.name} | " + "; ".join(drift_detail),
                )
            )
    return failures


# ---------------------------------------------------------------------------
# N99 — VSResourceSchemaConformance scorer (registered with the canonical
# scorer registry so the build pipeline can discover it).
# ---------------------------------------------------------------------------


from brickvision_runtime.eval.scorers import (  # noqa: E402
    ScorerResult,
    register_scorer,
)


@register_scorer(skill_id="install.vector_search", name="resource_schema_conformance")
def vs_resource_schema_conformance(
    *,
    declared: Sequence[VSIndexSpec],
    observed: Mapping[str, VSIndexProbe],
) -> ScorerResult:
    """N99 — eval-style wrapper for ``check_vector_search``.

    Score 1.0 on no drift, 0.0 otherwise; emits the failures'
    reason codes so the dashboard's top-failures tile can render
    them directly.
    """

    failures = check_vector_search(declared=declared, observed=observed)
    if not failures:
        return ScorerResult(
            score=1.0,
            reason_codes=(),
            details={
                "label": "vs_resource_schema_conformance",
                "declared": len(declared),
                "drifted": 0,
            },
        )
    seen_codes: list[str] = []
    for f in failures:
        if f.reason_code.value not in seen_codes:
            seen_codes.append(f.reason_code.value)
    return ScorerResult(
        score=0.0,
        reason_codes=tuple(seen_codes),
        details={
            "label": "vs_resource_schema_conformance",
            "declared": len(declared),
            "drifted": len(failures),
            "failures": [
                {
                    "reason_code": f.reason_code.value,
                    "suggested_next_action": f.suggested_next_action,
                    "detail": f.detail,
                }
                for f in failures
            ],
        },
    )


__all__ = [
    "VSIndexProbe",
    "VSIndexSpec",
    "check_vector_search",
    "vs_resource_schema_conformance",
]
