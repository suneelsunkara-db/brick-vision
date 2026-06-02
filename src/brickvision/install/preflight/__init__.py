"""Workspace-bound install pre-flights.

Each module here exposes a small, typed checker — usually a
function that takes an ``InstallManifest`` and returns
``PreFlightFailure | None`` (or ``list[PreFlightFailure]`` for
multi-failure checks) — so the runner in
``brickvision.cli.install`` can compose them into a deterministic
sequence.

Modules:

- ``vector_search`` — Vector Search OOB-provisioning + schema check.
- ``capability_graph`` — Capability Graph install gates (4 checks):
  indexer SP provisioned · budget namespace isolated · UC schema
  ownership · VS endpoint per-index grants
  (``docs/19-local-development.md`` §15.5).
- ``runtime_floors`` — runtime floor checks (Python, SDK, MLflow).
- ``uc_bindings`` — UC workspace-catalog binding pre-flight for the
  harness target catalog.

The pre-v0.7.7 ``lakehouse_monitoring`` and
``lakehouse_monitoring_embed`` checks were retired in v0.7.7 alongside
the End-Customer Console SPA emission. Lakehouse Monitoring is still
scheduled as part of the productionize stage; the embed-toggle
pre-flight only existed to gate the SPA's iframe panels and is no
longer load-bearing.
"""

from . import (
    capability_graph,
    runtime_floors,
    uc_bindings,
    vector_search,
)

__all__ = [
    "capability_graph",
    "runtime_floors",
    "uc_bindings",
    "vector_search",
]
