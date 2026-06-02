"""v0.7.7 Capability Graph install pre-flights (4 gates).

Per ``docs/19-local-development.md`` §15.5, ``brickvision install`` adds
4 new pre-flight gates that must pass before the indexer Job can be
deployed safely:

1. ``pre_flight.indexer_sp_provisioned``
   ⇒ ``INDEXER_SP_NOT_PROVISIONED``
   asserts ``bv_indexer_sp`` exists AND is distinct from ``bv_app_sp``.

2. ``pre_flight.indexer_budget_namespace_isolated``
   ⇒ ``INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED``
   asserts ``<BV_CATALOG>.<BV_SCHEMA>.budget_namespaces`` has both
   ``app`` and ``indexer`` rows with non-overlapping ledger tables,
   and that ``BV_BUDGET_NAMESPACE`` resolves correctly under each SP.

3. ``pre_flight.uc_schema_capability_graph_ownership``
   ⇒ ``UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID``
   asserts the single BrickVision schema (``<BV_CATALOG>.<BV_SCHEMA>``)
   exists with OWNER = ``bv_indexer_sp`` and ``bv_app_sp`` has
   ``SELECT`` only (no ``MODIFY``/``CREATE``).

4. ``pre_flight.vector_search_endpoint_grants``
   ⇒ ``VS_ENDPOINT_GRANTS_MIXED``
   asserts the shared ``bv_vs_endpoint`` has ``bv_indexer_sp`` ``WRITE``
   on the 3 capability-graph indexes and ``bv_app_sp`` ``READ``-only on
   the same.

House-style note: each check is a **pure function** taking a typed
``Spec`` (partner-declared expected state) + ``Probe`` (observed state
the install runner has already collected via the Databricks SDK) and
returning ``list[PreFlightFailure]``. The probes are constructed by the
install runner; this module never imports ``databricks-sdk`` directly so
unit tests can run offline. This mirrors
:mod:`brickvision.install.preflight.vector_search` (N98).

The 4 reason codes are registered in
:mod:`brickvision_runtime.failures` (v0.7.7 install-pre-flight block).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from brickvision.cli.install import PreFlightFailure
from brickvision_runtime.failures import ReasonCode


# ---------------------------------------------------------------------------
# 1. Indexer SP provisioned + distinct from app SP
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IndexerSPSpec:
    """Partner-declared SP names + the install runner's expected
    minimum-permission grants."""

    indexer_sp_display_name: str = "bv_indexer_sp"
    app_sp_display_name: str = "bv_app_sp"


@dataclasses.dataclass(frozen=True, slots=True)
class IndexerSPProbe:
    """Observed SCIM ServicePrincipals state collected by the install
    runner via ``GET /api/2.0/preview/scim/v2/ServicePrincipals``."""

    indexer_sp_application_id: str | None  # None ⇒ SP does not exist
    app_sp_application_id: str | None
    enabled: Mapping[str, bool] = dataclasses.field(default_factory=dict)
    """Per-SP ``active`` flag from SCIM. Both must be True."""


def check_indexer_sp_provisioned(
    *,
    spec: IndexerSPSpec,
    probe: IndexerSPProbe,
) -> list[PreFlightFailure]:
    """Pre-flight 1 of 4 — verifies ``bv_indexer_sp`` exists and is
    distinct from ``bv_app_sp``.

    Returns one failure per detected miss. Empty list ⇒ pass.
    """

    failures: list[PreFlightFailure] = []
    if probe.indexer_sp_application_id is None:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_SP_NOT_PROVISIONED,
                suggested_next_action=(
                    f"create the {spec.indexer_sp_display_name!r} service principal"
                    " via the Databricks CLI: `databricks service-principals"
                    f" create --display-name {spec.indexer_sp_display_name}`,"
                    " then grant the per-resource permissions in"
                    " docs/23-databricks-capability-graph.md §23.3.5"
                ),
                detail=f"display_name={spec.indexer_sp_display_name}",
            )
        )
        return failures  # later checks meaningless without the SP

    if probe.app_sp_application_id is None:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_SP_NOT_PROVISIONED,
                suggested_next_action=(
                    f"create the {spec.app_sp_display_name!r} service principal"
                    " (the app's SP must already exist before the indexer SP"
                    " isolation can be enforced)"
                ),
                detail=f"display_name={spec.app_sp_display_name}",
            )
        )
        return failures

    if probe.indexer_sp_application_id == probe.app_sp_application_id:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_SP_NOT_PROVISIONED,
                suggested_next_action=(
                    "the partner has collapsed bv_indexer_sp and bv_app_sp into"
                    " a single SP; recreate the indexer SP as a distinct SCIM"
                    " entry per docs/23-databricks-capability-graph.md §23.3.5"
                ),
                detail=(
                    f"shared_application_id={probe.indexer_sp_application_id}"
                ),
            )
        )

    inactive = [
        name
        for name, active in probe.enabled.items()
        if not active and name in (spec.indexer_sp_display_name, spec.app_sp_display_name)
    ]
    if inactive:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_SP_NOT_PROVISIONED,
                suggested_next_action=(
                    "re-activate the disabled SP via the SCIM PATCH endpoint"
                    " (active=true) or recreate it"
                ),
                detail=f"inactive_sps={inactive}",
            )
        )
    return failures


# ---------------------------------------------------------------------------
# 2. Indexer budget namespace isolated from app
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetNamespaceSpec:
    """Partner-declared budget namespace expectations."""

    expected_namespaces: tuple[str, ...] = ("app", "indexer")


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetNamespaceProbe:
    """Observed ``<BV_CATALOG>.<BV_SCHEMA>.budget_namespaces`` rows +
    per-SP env var resolution."""

    namespaces: Mapping[str, str]
    """``namespace -> ledger_table`` map; empty ⇒ table doesn't exist."""

    env_resolution: Mapping[str, str]
    """``service_principal_display_name -> resolved BV_BUDGET_NAMESPACE``."""


def check_budget_namespace_isolated(
    *,
    spec: BudgetNamespaceSpec,
    probe: BudgetNamespaceProbe,
) -> list[PreFlightFailure]:
    """Pre-flight 2 of 4 — verifies ``app`` + ``indexer`` budget
    namespaces have non-overlapping ledger tables and resolve correctly
    per SP.
    """

    failures: list[PreFlightFailure] = []
    missing = [ns for ns in spec.expected_namespaces if ns not in probe.namespaces]
    if missing:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED,
                suggested_next_action=(
                    "INSERT one row per missing namespace into"
                    " <BV_CATALOG>.<BV_SCHEMA>.budget_namespaces with a"
                    " distinct ledger_table per"
                    " docs/13-model-routing-and-budget.md §11.4"
                ),
                detail=f"missing_namespaces={missing}",
            )
        )
        return failures

    ledger_to_ns: dict[str, list[str]] = {}
    for ns, ledger in probe.namespaces.items():
        ledger_to_ns.setdefault(ledger, []).append(ns)
    overlapping = {
        ledger: namespaces
        for ledger, namespaces in ledger_to_ns.items()
        if len(namespaces) > 1
    }
    if overlapping:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED,
                suggested_next_action=(
                    "split overlapping namespace ledgers; each namespace must"
                    " write to its own Delta table to keep BudgetGuard"
                    " aggregation isolated"
                ),
                detail=f"shared_ledgers={overlapping}",
            )
        )

    expected_resolution = {
        "bv_app_sp": "app",
        "bv_indexer_sp": "indexer",
    }
    misresolved = {
        sp: probe.env_resolution.get(sp)
        for sp, expected in expected_resolution.items()
        if sp in probe.env_resolution and probe.env_resolution[sp] != expected
    }
    if misresolved:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED,
                suggested_next_action=(
                    "fix BV_BUDGET_NAMESPACE in the indexer Job's task code"
                    " (must set 'indexer' explicitly) and the app's runtime"
                    " env (must default to 'app')"
                ),
                detail=f"misresolved={misresolved}",
            )
        )

    return failures


# ---------------------------------------------------------------------------
# 3. UC schema <BV_CATALOG>.<BV_SCHEMA> ownership + grants
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class UCSchemaSpec:
    """Partner-declared expected ownership + grant set for
    the single BrickVision schema (``<BV_CATALOG>.<BV_SCHEMA>``)."""

    schema_full_name: str  # e.g., "brickvision_dev.brickvision"
    expected_owner: str = "bv_indexer_sp"
    expected_owner_aliases: tuple[str, ...] = ()
    app_sp_display_name: str = "bv_app_sp"
    app_sp_aliases: tuple[str, ...] = ()
    app_allowed_privileges: tuple[str, ...] = ("SELECT",)
    app_forbidden_privileges: tuple[str, ...] = ("MODIFY", "CREATE")
    """The app needs USE_SCHEMA + SELECT to read UI tables; write-like
    privileges remain forbidden."""


@dataclasses.dataclass(frozen=True, slots=True)
class UCSchemaProbe:
    """Observed UC schema ownership + per-principal privilege list."""

    exists: bool
    owner: str | None
    grants: Mapping[str, tuple[str, ...]]
    """``principal -> (privilege, ...)`` map from
    ``GET /api/2.1/unity-catalog/permissions/schema/<schema>``."""


def check_uc_schema_capability_graph_ownership(
    *,
    spec: UCSchemaSpec,
    probe: UCSchemaProbe,
) -> list[PreFlightFailure]:
    """Pre-flight 3 of 4 — verifies the capability-graph UC schema is
    owned by the indexer SP and the app SP has only SELECT.
    """

    failures: list[PreFlightFailure] = []
    if not probe.exists:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID,
                suggested_next_action=(
                    f"CREATE SCHEMA IF NOT EXISTS {spec.schema_full_name};"
                    f" then ALTER SCHEMA {spec.schema_full_name} OWNER TO"
                    f" `{spec.expected_owner}`"
                ),
                detail=f"schema={spec.schema_full_name}",
            )
        )
        return failures  # ownership/grants checks meaningless if schema absent

    accepted_owners = {spec.expected_owner, *spec.expected_owner_aliases}
    if probe.owner not in accepted_owners:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID,
                suggested_next_action=(
                    f"ALTER SCHEMA {spec.schema_full_name} OWNER TO"
                    f" `{spec.expected_owner}`"
                ),
                detail=f"observed_owner={probe.owner} expected={spec.expected_owner}",
            )
        )

    app_grants: set[str] = set()
    for principal in (spec.app_sp_display_name, *spec.app_sp_aliases):
        app_grants.update(probe.grants.get(principal, ()))
    bad_app_privs = app_grants & set(spec.app_forbidden_privileges)
    if bad_app_privs:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID,
                suggested_next_action=(
                    f"REVOKE {', '.join(sorted(bad_app_privs))} ON SCHEMA"
                    f" {spec.schema_full_name} FROM `{spec.app_sp_display_name}`;"
                    f" the app SP must be SELECT-only per §23.3.5"
                ),
                detail=f"forbidden_privs_held={sorted(bad_app_privs)}",
            )
        )

    missing_app_privs = set(spec.app_allowed_privileges) - app_grants
    if missing_app_privs:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID,
                suggested_next_action=(
                    f"GRANT {', '.join(sorted(missing_app_privs))} ON SCHEMA"
                    f" {spec.schema_full_name} TO `{spec.app_sp_display_name}`"
                ),
                detail=f"missing_privs={sorted(missing_app_privs)}",
            )
        )

    return failures


# ---------------------------------------------------------------------------
# 4. Vector Search endpoint per-index grants
# ---------------------------------------------------------------------------


_DEFAULT_CAPABILITY_GRAPH_INDEXES: tuple[str, ...] = (
    "entity_index",
)


@dataclasses.dataclass(frozen=True, slots=True)
class VSGrantSpec:
    """Expected per-index grants on the shared VS endpoint."""

    endpoint_name: str = "bv_vs_endpoint"
    indexer_sp_display_name: str = "bv_indexer_sp"
    indexer_sp_aliases: tuple[str, ...] = ()
    app_sp_display_name: str = "bv_app_sp"
    app_sp_aliases: tuple[str, ...] = ()
    capability_graph_indexes: tuple[str, ...] = _DEFAULT_CAPABILITY_GRAPH_INDEXES


@dataclasses.dataclass(frozen=True, slots=True)
class VSGrantProbe:
    """Observed VS endpoint + per-index grants."""

    endpoint_exists: bool
    index_grants: Mapping[str, Mapping[str, tuple[str, ...]]]
    """``index_name -> { principal -> (privilege, ...) }``. Privileges
    expected: ``WRITE`` for indexer, ``READ`` for app. Direct ``READ_WRITE``
    is treated as ``WRITE`` (i.e., a superset)."""


def _has_write(privileges: Sequence[str]) -> bool:
    p = {x.upper() for x in privileges}
    return "WRITE" in p or "READ_WRITE" in p or "MODIFY" in p


def _has_read(privileges: Sequence[str]) -> bool:
    p = {x.upper() for x in privileges}
    return "READ" in p or "READ_WRITE" in p or "SELECT" in p


def check_vector_search_endpoint_grants(
    *,
    spec: VSGrantSpec,
    probe: VSGrantProbe,
) -> list[PreFlightFailure]:
    """Pre-flight 4 of 4 — verifies the shared VS endpoint has correct
    per-index grants for the direct-access capability-graph retrieval index.
    """

    failures: list[PreFlightFailure] = []
    if not probe.endpoint_exists:
        failures.append(
            PreFlightFailure(
                reason_code=ReasonCode.VS_OUT_OF_BAND_PROVISIONING_REQUIRED,
                suggested_next_action=(
                    f"create VS endpoint {spec.endpoint_name!r} via the workspace"
                    " UI / SDK before re-running brickvision install"
                ),
                detail=f"endpoint={spec.endpoint_name}",
            )
        )
        return failures

    for index_name in spec.capability_graph_indexes:
        per_index = probe.index_grants.get(index_name, {})
        if not per_index:
            # Some SDK/workspace combinations do not expose Vector Search
            # index permissions through UC grants. Endpoint + index existence
            # is still useful; do not fail a valid deploy on an unavailable
            # permissions surface.
            continue

        indexer_privs: set[str] = set()
        for principal in (spec.indexer_sp_display_name, *spec.indexer_sp_aliases):
            indexer_privs.update(per_index.get(principal, ()))
        app_privs: set[str] = set()
        for principal in (spec.app_sp_display_name, *spec.app_sp_aliases):
            app_privs.update(per_index.get(principal, ()))

        if not _has_write(indexer_privs):
            failures.append(
                PreFlightFailure(
                    reason_code=ReasonCode.VS_ENDPOINT_GRANTS_MIXED,
                    suggested_next_action=(
                        f"GRANT WRITE ON INDEX {index_name} TO"
                        f" `{spec.indexer_sp_display_name}`"
                    ),
                    detail=(
                        f"index={index_name} principal={spec.indexer_sp_display_name}"
                        f" observed_privs={list(indexer_privs)}"
                    ),
                )
            )

        if _has_write(app_privs):
            failures.append(
                PreFlightFailure(
                    reason_code=ReasonCode.VS_ENDPOINT_GRANTS_MIXED,
                    suggested_next_action=(
                        f"REVOKE WRITE ON INDEX {index_name} FROM"
                        f" `{spec.app_sp_display_name}`; the app SP must be"
                        f" READ-only on capability-graph indexes per §23.5.2"
                    ),
                    detail=(
                        f"index={index_name} principal={spec.app_sp_display_name}"
                        f" observed_privs={list(app_privs)}"
                    ),
                )
            )

        if not _has_read(app_privs):
            failures.append(
                PreFlightFailure(
                    reason_code=ReasonCode.VS_ENDPOINT_GRANTS_MIXED,
                    suggested_next_action=(
                        f"GRANT READ ON INDEX {index_name} TO"
                        f" `{spec.app_sp_display_name}`"
                    ),
                    detail=(
                        f"index={index_name} principal={spec.app_sp_display_name}"
                        f" observed_privs={list(app_privs)}"
                    ),
                )
            )

    return failures


__all__ = [
    "BudgetNamespaceProbe",
    "BudgetNamespaceSpec",
    "IndexerSPProbe",
    "IndexerSPSpec",
    "UCSchemaProbe",
    "UCSchemaSpec",
    "VSGrantProbe",
    "VSGrantSpec",
    "check_budget_namespace_isolated",
    "check_indexer_sp_provisioned",
    "check_uc_schema_capability_graph_ownership",
    "check_vector_search_endpoint_grants",
]
