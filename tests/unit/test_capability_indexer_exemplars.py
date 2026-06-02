from __future__ import annotations

import json
from pathlib import Path

import pytest

from brickvision_runtime.capability_graph import graph_builder
from brickvision_runtime.capability_graph.exemplars import load_skill
from brickvision_runtime.capability_graph.publish import _DEFAULT_PUBLISHED_TABLES
from brickvision_runtime.capability_graph.sources.openapi_adapter import (
    OpenAPIAdapterResult,
    OpenAPIOperationEntity,
)
from brickvision_runtime.capability_graph.sources.sdk_adapter import (
    SDKAdapterResult,
    SDKMethodEntity,
)
from brickvision_runtime.databricks_jobs import run_capability_indexer


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_load_hand_authored_skill_specs_falls_back_to_skills_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "demo.skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.yaml").write_text(
        "\n".join(
            [
                'id: "skill:demo.skill"',
                'version: "0.1.0"',
                'exemplar_of: "meta:delta-lake/ext:demo-skill"',
                'title: "Demo skill"',
                'owner: "brickvision"',
                'signing_key_id: "uc:secrets:bv/demo"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("BV_INDEXER_HAND_AUTHORED_MANIFEST", raising=False)
    monkeypatch.setenv("BV_INDEXER_SKILLS_DIR", str(skills_dir))

    specs = run_capability_indexer._load_hand_authored_skill_specs(graph_builder)

    assert specs == (
        graph_builder.HandAuthoredSkillSpec(
            skill_id="skill:demo.skill",
            exemplar_of="meta:delta-lake/ext:demo-skill",
            title="Demo skill",
        ),
    )


def test_load_hand_authored_skill_specs_uses_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "skill_id": "skill:manifest.skill",
                    "exemplar_of": "meta:delta-lake/ext:manifest-skill",
                    "title": "Manifest skill",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BV_INDEXER_HAND_AUTHORED_MANIFEST", str(manifest))

    specs = run_capability_indexer._load_hand_authored_skill_specs(graph_builder)

    assert specs == (
        graph_builder.HandAuthoredSkillSpec(
            skill_id="skill:manifest.skill",
            exemplar_of="meta:delta-lake/ext:manifest-skill",
            title="Manifest skill",
        ),
    )


def test_load_hand_authored_skill_specs_fails_missing_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "BV_INDEXER_HAND_AUTHORED_MANIFEST",
        str(tmp_path / "missing.json"),
    )

    with pytest.raises(RuntimeError, match="does not exist"):
        run_capability_indexer._load_hand_authored_skill_specs(graph_builder)


def test_build_capability_graph_mints_live_skill_exemplar_extensions() -> None:
    skills_dir = REPO_ROOT / "skills"
    specs = []
    expected_extensions = set()
    for skill_yaml in sorted(skills_dir.glob("*/SKILL.yaml")):
        skill = load_skill(skill_yaml.parent)
        specs.append(
            graph_builder.HandAuthoredSkillSpec(
                skill_id=skill.skill_id,
                exemplar_of=skill.exemplar_of,
                title=str(skill.ir.get("title") or "") or None,
            )
        )
        expected_extensions.add(skill.exemplar_of)

    result = graph_builder.build_capability_graph(
        sdk_result=None,
        openapi_result=None,
        docs_result=None,
        blog_result=None,
        labs_result=None,
        hand_authored_skills=tuple(specs),
        snapshot_id="snap_test",
        built_at_ms=1,
        now_ms=1,
        corpus_hash="test",
    )

    extension_ids = {row.extension_id for row in result.extensions}
    assert expected_extensions <= extension_ids
    assert not result.broken_exemplar_pointers
    assert result.build_telemetry["hand_authored_exemplars_linked"] == len(specs)
    assert {
        row.entity_id for row in result.source_provenance
        if row.source_kind == "hand_authored"
    } >= expected_extensions
    assert all("hand_authored" not in row.source_kinds for row in result.meta_skills)
    assert {
        (row.edge_kind, row.weight) for row in result.entity_edges
        if row.src_id.startswith("skill:")
    } == {("exemplifies", 0.0)}


def test_source_provenance_publish_key_preserves_each_reference() -> None:
    source_provenance = next(
        table for table in _DEFAULT_PUBLISHED_TABLES
        if table.source_table == "source_provenance"
    )

    assert source_provenance.primary_key_columns == (
        "snapshot_id",
        "entity_id",
        "source_kind",
        "ref",
        "content_hash",
    )


def test_sdk_execution_boundary_methods_merge_into_skill_anchors() -> None:
    sdk = SDKAdapterResult(
        sdk_version="0.test",
        sdk_root="/tmp/sdk",
        parsed_at_ms=1,
        modules=(),
        services=(),
        methods=(
            _sdk_method("sql", "StatementExecutionAPI", "execute_statement"),
            _sdk_method("jobs", "JobsAPI", "submit"),
            _sdk_method("catalog", "RegisteredModelsAPI", "set_alias"),
        ),
        parse_errors=(),
    )

    result = graph_builder.build_capability_graph(
        sdk_result=sdk,
        openapi_result=None,
        docs_result=None,
        blog_result=None,
        labs_result=None,
        hand_authored_skills=(
            graph_builder.HandAuthoredSkillSpec(
                skill_id="skill:databricks.statement-execute",
                exemplar_of="meta:databricks-sql/ext:statement-execution-api",
            ),
            graph_builder.HandAuthoredSkillSpec(
                skill_id="skill:lakeflow.jobs-run-submit",
                exemplar_of="meta:lakeflow-jobs/ext:jobs-runs-submit",
            ),
            graph_builder.HandAuthoredSkillSpec(
                skill_id="skill:ml.assign-alias",
                exemplar_of="meta:model-registry/ext:assign-production-alias",
            ),
        ),
        snapshot_id="snap_test",
        built_at_ms=2,
        now_ms=2,
        corpus_hash="test",
    )

    source_kinds = _source_kinds_by_extension(result)
    assert source_kinds["meta:databricks-sql/ext:statement-execution-api"] == {
        "hand_authored",
        "sdk",
    }
    assert source_kinds["meta:lakeflow-jobs/ext:jobs-runs-submit"] == {
        "hand_authored",
        "sdk",
    }
    assert source_kinds["meta:model-registry/ext:assign-production-alias"] == {
        "hand_authored",
        "sdk",
    }
    assert "meta:compute/ext:execute-statement" not in {
        row.extension_id for row in result.extensions
    }
    assert "meta:lakeflow-jobs/ext:submit" not in {
        row.extension_id for row in result.extensions
    }
    assert "meta:unity-catalog-foundation/ext:set-alias" not in {
        row.extension_id for row in result.extensions
    }


def test_api_reference_operations_route_to_execution_skill_anchors() -> None:
    openapi = OpenAPIAdapterResult(
        parsed_at_ms=1,
        documents=(),
        operations=(
            _openapi_operation(
                operation_id_raw="statementexecution_executestatement",
                path="/api/2.0/sql/statements",
                method="POST",
            ),
            _openapi_operation(
                operation_id_raw="jobs-runs-submit",
                path="/api/2.1/jobs/runs/submit",
                method="POST",
            ),
            _openapi_operation(
                operation_id_raw="registeredmodels_setalias",
                path="/api/2.0/mlflow/registered-models/alias",
                method="POST",
            ),
        ),
        schemas=(),
        security_schemes=(),
        parse_errors=(),
    )

    result = graph_builder.build_capability_graph(
        sdk_result=None,
        openapi_result=openapi,
        docs_result=None,
        blog_result=None,
        labs_result=None,
        hand_authored_skills=(),
        snapshot_id="snap_test",
        built_at_ms=2,
        now_ms=2,
        corpus_hash="test",
    )

    source_kinds = _source_kinds_by_extension(result)
    assert source_kinds["meta:databricks-sql/ext:statement-execution-api"] == {
        "openapi",
    }
    assert source_kinds["meta:lakeflow-jobs/ext:jobs-runs-submit"] == {
        "openapi",
    }
    assert source_kinds["meta:model-registry/ext:assign-production-alias"] == {
        "openapi",
    }


def test_api_reference_slug_canonicalization_uses_versioned_rest_paths() -> None:
    assert run_capability_indexer._canonical_api_reference_operation(
        "statementexecution",
        "executestatement",
    ) == (
        "POST",
        "/api/2.0/sql/statements",
        "statementexecution_executestatement",
        "2.0",
    )
    assert run_capability_indexer._canonical_api_reference_operation(
        "jobs_21",
        "submit",
    ) == (
        "POST",
        "/api/2.1/jobs/runs/submit",
        "jobs-runs-submit",
        "2.1",
    )


def _sdk_method(module: str, service_class: str, method_name: str) -> SDKMethodEntity:
    method_id = f"sdk:{module}.{service_class}.{method_name}"
    return SDKMethodEntity(
        method_id=method_id,
        module_name=module,
        service_class_name=service_class,
        method_name=method_name,
        signature=f"def {method_name}(self): ...",
        docstring=f"{method_name} docstring",
        effect_class="write",
        effect_verb_matched=method_name.split("_")[0],
        deprecated_in_docstring=False,
        source_file=f"{module}.py",
        source_line=1,
        content_hash=f"hash-{module}-{service_class}-{method_name}",
    )


def _openapi_operation(
    *,
    operation_id_raw: str,
    path: str,
    method: str,
) -> OpenAPIOperationEntity:
    return OpenAPIOperationEntity(
        operation_id=f"openapi:2.0:{operation_id_raw}",
        api_version="2.0",
        operation_id_raw=operation_id_raw,
        path=path,
        http_method=method,
        summary=operation_id_raw,
        description=operation_id_raw,
        effect_class_raw=None,
        effect_class="write",
        request_schema_refs=(),
        response_schema_refs=(),
        security_scheme_refs=(),
        deprecated=False,
        tags=(),
        source_url=f"https://docs.databricks.com/api/workspace/{operation_id_raw}",
        content_hash=f"hash-{operation_id_raw}",
    )


def _source_kinds_by_extension(result: graph_builder.CapabilityGraphBuildResult) -> dict[str, set[str]]:
    source_kinds: dict[str, set[str]] = {}
    for row in result.source_provenance:
        source_kinds.setdefault(row.entity_id, set()).add(row.source_kind)
    return source_kinds
