#!/usr/bin/env python3
"""Validate the live evidence substrate used by Build Suggestions.

This script reads the active Capability Graph snapshot and current Workspace
Context from Lakebase, then runs pure quality checks. It intentionally does not
repair data, generate suggestions, or execute builds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from brickvision_runtime.capability_graph.exemplars import (  # noqa: E402
    load_skill,
    walk_hand_authored_skills,
)
from brickvision_runtime.evidence_quality import (  # noqa: E402
    validate_skill_anchor_resolution,
    validate_workspace_claim_quality,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-quality-errors", action="store_true")
    args = parser.parse_args()

    _load_env_file(REPO_ROOT / args.env_file)
    payload = _load_live_evidence_payload()
    skill_exemplars = walk_hand_authored_skills(REPO_ROOT / "skills")
    manifest_report = _audit_skill_manifests(REPO_ROOT / "skills")

    anchor_report = validate_skill_anchor_resolution(
        skill_exemplars=skill_exemplars,
        extension_ids=payload["extension_ids"],
        extension_source_kinds=payload["extension_source_kinds"],
        require_source_grounding=True,
    )
    workspace_report = validate_workspace_claim_quality(
        claims=payload["workspace_claims"],
    )
    report = {
        "active_snapshot_id": payload["active_snapshot_id"],
        "skill_anchor_resolution": {
            "passed": anchor_report.passed,
            "reason_codes": list(anchor_report.reason_codes),
            **anchor_report.details,
        },
        "skill_manifest_contracts": manifest_report,
        "workspace_claim_quality": {
            "passed": workspace_report.passed,
            "reason_codes": list(workspace_report.reason_codes),
            **workspace_report.details,
        },
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)

    if args.fail_on_quality_errors:
        return 0 if (
            anchor_report.passed
            and manifest_report["passed"]
            and workspace_report.passed
        ) else 1
    return 0


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _load_live_evidence_payload() -> dict[str, Any]:
    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    import psycopg  # type: ignore[import-not-found]  # noqa: PLC0415

    client = WorkspaceClient()
    branch = (
        f"projects/{os.environ['BV_LAKEBASE_PROJECT_ID']}"
        f"/branches/{os.environ.get('BV_LAKEBASE_BRANCH', 'production')}"
    )
    endpoint = list(client.postgres.list_endpoints(parent=branch))[0]
    host = endpoint.status.hosts.host
    credential = client.postgres.generate_database_credential(endpoint=endpoint.name)
    token = getattr(credential, "token", None) or getattr(credential, "credential", None)
    me = client.current_user.me()
    principal = (me.user_name or me.display_name or "").strip()
    schema = _sanitize_identifier(os.environ.get("BV_SCHEMA", "brickvision"))

    with psycopg.connect(
        host=host,
        port=5432,
        dbname=os.environ["BV_LAKEBASE_DATABASE"],
        user=principal,
        password=token,
        sslmode="require",
        autocommit=True,
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 10000")
            cur.execute(
                f"""
                SELECT snapshot_id
                FROM {schema}.active_snapshot_id_synced
                WHERE singleton_key = %s
                """,
                ("singleton",),
            )
            snapshot_row = cur.fetchone()
            if not snapshot_row:
                raise RuntimeError("active_snapshot_id_synced has no singleton row")
            snapshot_id = str(snapshot_row[0])

            cur.execute(
                f"""
                SELECT extension_id
                FROM {schema}.extensions_synced
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            extension_ids = [str(row[0]) for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT entity_id, source_kind
                FROM {schema}.source_provenance_synced
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            extension_source_kinds: dict[str, set[str]] = {}
            for row in cur.fetchall():
                entity_id = str(row[0])
                source_kind = str(row[1])
                if entity_id and source_kind:
                    extension_source_kinds.setdefault(entity_id, set()).add(source_kind)

            cur.execute(
                f"""
                SELECT subject, subject_kind, predicate, source_skill_id
                FROM {schema}.workspace_claims_current_synced
                """,
            )
            workspace_claims = [
                {
                    "subject": str(row[0]),
                    "subject_kind": str(row[1]),
                    "predicate": str(row[2]),
                    "source_skill_id": str(row[3]),
                }
                for row in cur.fetchall()
            ]

    return {
        "active_snapshot_id": snapshot_id,
        "extension_ids": extension_ids,
        "extension_source_kinds": {
            entity_id: sorted(source_kinds)
            for entity_id, source_kinds in extension_source_kinds.items()
        },
        "workspace_claims": workspace_claims,
    }


def _sanitize_identifier(name: str) -> str:
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _audit_skill_manifests(skills_dir: Path) -> dict[str, Any]:
    missing_capability_links: list[dict[str, str]] = []
    primary_link_mismatches: list[dict[str, Any]] = []
    skill_count = 0

    for skill_yaml in sorted(skills_dir.glob("*/SKILL.yaml")):
        skill_count += 1
        skill = load_skill(skill_yaml.parent)
        skill_id = str(skill.skill_id)
        exemplar_of = str(skill.exemplar_of or "")
        capability_links = skill.ir.get("capability_links")
        if not isinstance(capability_links, dict) or not capability_links:
            missing_capability_links.append(
                {"skill_id": skill_id, "exemplar_of": exemplar_of}
            )
            continue
        primary_links = capability_links.get("primary")
        if isinstance(primary_links, str):
            normalized_primary = [primary_links]
        elif isinstance(primary_links, list):
            normalized_primary = [str(item) for item in primary_links]
        else:
            normalized_primary = []
        if exemplar_of and exemplar_of not in normalized_primary:
            primary_link_mismatches.append(
                {
                    "skill_id": skill_id,
                    "exemplar_of": exemplar_of,
                    "primary": normalized_primary,
                }
            )

    reason_codes: list[str] = []
    if missing_capability_links:
        reason_codes.append("SKILL_CAPABILITY_LINKS_MISSING")
    if primary_link_mismatches:
        reason_codes.append("SKILL_CAPABILITY_LINKS_PRIMARY_MISMATCH")

    return {
        "passed": not reason_codes,
        "reason_codes": reason_codes,
        "skill_count": skill_count,
        "missing_capability_links_count": len(missing_capability_links),
        "missing_capability_links": missing_capability_links,
        "primary_link_mismatch_count": len(primary_link_mismatches),
        "primary_link_mismatches": primary_link_mismatches,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"active_snapshot_id: {report['active_snapshot_id']}")
    anchor = report["skill_anchor_resolution"]
    print(
        "skill_anchor_resolution: "
        f"{anchor['resolved_count']}/{anchor['skill_count']} resolved; "
        f"missing={anchor['missing_count']}; "
        f"ungrounded={anchor['ungrounded_count']}"
    )
    for item in anchor["missing"][:20]:
        print(f"  missing: {item['skill_id']} -> {item['anchor']}")
    for item in anchor["ungrounded"][:20]:
        print(
            "  ungrounded: "
            f"{item['skill_id']} -> {item['anchor']} "
            f"source_kinds={item['source_kinds']}"
        )

    manifest = report["skill_manifest_contracts"]
    print(
        "skill_manifest_contracts: "
        f"missing_capability_links={manifest['missing_capability_links_count']}; "
        f"primary_link_mismatches={manifest['primary_link_mismatch_count']}"
    )
    for item in manifest["missing_capability_links"][:20]:
        print(f"  missing capability_links: {item['skill_id']} -> {item['exemplar_of']}")

    workspace = report["workspace_claim_quality"]
    print(
        "workspace_claim_quality: "
        f"claims={workspace['claim_count']}; "
        f"subject_kind_mismatches={workspace['subject_kind_mismatch_count']}; "
        f"missing_profile_predicates={workspace['missing_profile_predicates']}"
    )
    print(f"  by_kind={workspace['by_kind']}")
    print(f"  by_predicate={workspace['by_predicate']}")
    for item in workspace["subject_kind_mismatches"][:20]:
        print(
            "  kind mismatch: "
            f"{item['subject']} observed={item['observed_subject_kind']} "
            f"expected={item['expected_subject_kind']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
