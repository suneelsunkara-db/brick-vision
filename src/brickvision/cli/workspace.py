"""``brickvision workspace`` — Workspace KG runtime commands."""

from __future__ import annotations

import argparse
import dataclasses
import json

from brickvision_runtime.skills.uc_catalog_introspect import run_uc_catalog_introspect


def add_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="workspace_command", required=True)
    introspect = sub.add_parser(
        "introspect-uc",
        help="Inspect Unity Catalog and emit Workspace KG claims.",
    )
    introspect.add_argument("--workspace-profile-id", default=None)
    introspect.add_argument("--workspace-id", default=None)
    introspect.add_argument("--catalog-filter", default=None)
    introspect.add_argument("--include-system", action="store_true")
    introspect.add_argument("--config-hash", default=None)
    introspect.add_argument("--run-id", default=None)
    introspect.set_defaults(_handler=_introspect_uc)


def _introspect_uc(args: argparse.Namespace) -> int:
    result = run_uc_catalog_introspect(
        workspace_profile_id=args.workspace_profile_id,
        workspace_id=args.workspace_id,
        include_system=bool(args.include_system),
        catalog_filter=args.catalog_filter,
        config_hash=args.config_hash,
        run_id=args.run_id,
    )
    print(json.dumps(dataclasses.asdict(result), sort_keys=True))
    return 0


__all__ = ["add_parser"]
