"""BrickVision partner-side Console API sidecar.

FastAPI adapter that bridges the ``apps/console`` React SPA to the
``brickvision_runtime`` package and the partner's Databricks workspace
via OBO. This package is deliberately thin — it does no business
logic of its own; every meaningful action delegates to a runtime
service.

See ``docs/12-visual-builder.md`` §10.2 + §10.7.7.B and
``docs/08-transpiler.md`` §7.8.E.1.
"""

__version__ = "0.6.0.dev0"
