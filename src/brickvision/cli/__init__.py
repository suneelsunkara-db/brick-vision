"""Build-time ``brickvision`` CLI entry points.

Per ``docs/19-local-development.md`` §15.5-§15.6. The CLI is the
single deterministic install path. v0.7.7 narrowed the surface to
the load-bearing commands only:

- ``brickvision install``    — install pre-flights (N74)
- ``brickvision uninstall``  — DROP SCHEMA CASCADE
- ``brickvision indexer``    — capability-graph indexer
  (refresh / rollback / status / health)
- ``brickvision workspace``  — workspace KG introspection commands
- ``brickvision evaluation`` — MLflow dataset sync + scorer Job commands

Run via ``python -m brickvision.cli ...``.
"""

from __future__ import annotations

import argparse
import sys

from . import evaluation, indexer, install, uninstall, workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brickvision",
        description="BrickVision CLI (v0.7.7 capability-graph indexer + install).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install.add_parser(sub.add_parser("install", help=install.__doc__))
    uninstall.add_parser(sub.add_parser("uninstall", help=uninstall.__doc__))
    indexer.add_parser(sub.add_parser("indexer", help=indexer.__doc__))
    workspace.add_parser(sub.add_parser("workspace", help=workspace.__doc__))
    evaluation.add_parser(sub.add_parser("evaluation", help=evaluation.__doc__))

    args = parser.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.error("no handler bound for sub-command")
    return int(handler(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
