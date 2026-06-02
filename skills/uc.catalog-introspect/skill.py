"""Mechanical (DAG) skill: skill:uc.catalog-introspect (auto-generated)."""

# bv:templated:start id=imports
from __future__ import annotations

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG
# bv:templated:end id=imports


# bv:templated:start id=dag
def build_dag() -> DAG:
    return (
        DAG(name="skill:uc.catalog-introspect")
        .step(
            id="list_catalogs",
            tool="tool:uc.list_catalogs",
            inputs={"filter_pattern": "{catalog_filter}", "include_system": "{include_system}"},
        )
        .step(
            id="list_schemas",
            tool="tool:uc.list_schemas",
            for_each="list_catalogs.results",
            inputs={"catalog_name": "$list_catalogs.catalog_name"},
        )
        .step(
            id="list_tables",
            tool="tool:uc.list_tables",
            for_each="list_schemas.results",
            inputs={"catalog_name": "$list_schemas.catalog_name", "schema_name": "$list_schemas.schema_name"},
        )
        .step(
            id="list_views",
            tool="tool:uc.list_views",
            for_each="list_schemas.results",
            inputs={"catalog_name": "$list_schemas.catalog_name", "schema_name": "$list_schemas.schema_name"},
        )
        .step(
            id="list_volumes",
            tool="tool:uc.list_volumes",
            for_each="list_schemas.results",
            inputs={"catalog_name": "$list_schemas.catalog_name", "schema_name": "$list_schemas.schema_name"},
        )
        .step(
            id="list_functions",
            tool="tool:uc.list_functions",
            for_each="list_schemas.results",
            inputs={"catalog_name": "$list_schemas.catalog_name", "schema_name": "$list_schemas.schema_name"},
        )
        .step(
            id="build_claims",
            kind="pure_python",
            function="build_uc_introspect_claims",
            inputs={"catalogs": "$list_catalogs.results", "schemas": "$list_schemas.results", "tables": "$list_tables.results", "views": "$list_views.results", "volumes": "$list_volumes.results", "functions": "$list_functions.results"},
        )
        .step(
            id="emit_claims",
            tool="tool:kg.emit_claims",
            inputs={"claims": "$build_claims.claims"},
        )
    )
# bv:templated:end id=dag


# bv:templated:start id=skill
SKILL = Skill.mechanical(
    id="skill:uc.catalog-introspect",
    version="0.1.0",
    dag=build_dag(),
    constitutional=["no.write.to.uc", "no.cross.workspace.read", "respect.user.obo.if.requested"],
)
# bv:templated:end id=skill


# The executable implementation lives in the runtime package; this checked-in
# skill module re-exports the runner so Skill Builder readiness can resolve it.
from brickvision_runtime.skills.uc_catalog_introspect import run_uc_catalog_introspect  # noqa: E402
