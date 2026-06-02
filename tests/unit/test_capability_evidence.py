from brickvision_runtime.capability_evidence import (
    has_contract_only_capability_evidence,
    source_grounded_capability_refs,
)


def test_source_grounded_refs_accept_indexed_source_kinds() -> None:
    refs = source_grounded_capability_refs(
        [
            {
                "entity_id": "meta:lakeflow-jobs/ext:jobs-runs-submit",
                "source_kinds": ["openapi", "docs"],
            }
        ]
    )

    assert refs == ["meta:lakeflow-jobs/ext:jobs-runs-submit"]


def test_source_grounded_refs_reject_hand_authored_only_anchor() -> None:
    evidence = [
        {
            "entity_id": "meta:lakeflow-jobs/ext:jobs-runs-submit",
            "source_kinds": ["hand_authored"],
        }
    ]

    assert source_grounded_capability_refs(evidence) == []
    assert has_contract_only_capability_evidence(evidence) is True


def test_source_grounded_refs_allow_raw_openapi_ref_without_metadata() -> None:
    refs = source_grounded_capability_refs(
        [
            {
                "operation_id": "openapi:2.1:JobsRunsSubmit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
            }
        ]
    )

    assert refs == ["openapi:2.1:JobsRunsSubmit"]

