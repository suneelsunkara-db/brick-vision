"""Clean 5 — production module with a clean filename + clean classes.

Module name describes the production purpose
(``capability_graph_persist``, ``customer_console_obo``, etc.), not
its test status. Class names describe the real Databricks resource
they wrap, not the mock they replace.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class CapabilityGraphSnapshot:
    """A single immutable capability-graph snapshot identifier."""

    snapshot_id: str
    corpus_hash: str
    promoted_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class StatementExecutionRequest:
    """A request to ``WorkspaceClient.statement_execution.execute_statement``."""

    warehouse_id: str
    statement: str
    catalog: str
    schema: str
