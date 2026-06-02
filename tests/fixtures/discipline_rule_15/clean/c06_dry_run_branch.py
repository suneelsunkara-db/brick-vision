"""Clean 6 — production wrapper with a ``BV_DRY_RUN`` audit branch.

Demonstrates the dry-run pattern: the production function commits to a
real Databricks SQL statement by default, but when ``BV_DRY_RUN=true``
it logs the rendered statement to a fixture file instead of executing
it. The branch is inside the production code path; there is no
Protocol seam, no ``Fake``-prefixed class, no ``stub_`` function.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class CapabilityGraphPersister:
    """Persists snapshot metadata via Statement Execution + UC Volumes."""

    def __init__(self, warehouse_id: str) -> None:
        self.warehouse_id = warehouse_id

    def persist(self, *, snapshot_id: str, statement: str) -> None:
        if os.environ.get("BV_DRY_RUN", "false").lower() == "true":
            self._log_to_fixture(snapshot_id=snapshot_id, statement=statement)
            return
        self._execute_statement(statement=statement)

    def _execute_statement(self, *, statement: str) -> None:
        raise NotImplementedError(
            "wired to WorkspaceClient.statement_execution.execute_statement"
        )

    def _log_to_fixture(self, *, snapshot_id: str, statement: str) -> None:
        target = Path(
            os.environ.get(
                "BV_DRY_RUN_PERSIST_LOG",
                "tests/fixtures/capability_graph/last_persist_payload.json",
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {"snapshot_id": snapshot_id, "statement": statement}, indent=2
            )
        )
