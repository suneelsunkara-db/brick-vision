"""Build-time install runbook (N98 / N100 / N105-N107).

Per [`docs/19-local-development.md`](../../../docs/19-local-development.md) §15.5.
The install runbook is **the single deterministic install path**;
sub-modules cover:

- ``preflight/`` — typed, runner-style pre-flights for every
  workspace-bound check (Vector Search, Lakehouse Monitoring,
  Mosaic Gateway, etc.).
- ``state.py``    — N106 per-step Claim emission to
  ``<bv>.install.events`` so ``brickvision install --resume-from``
  can pick up after any failed step.
- ``cli.py``      — N105 entry-point for ``brickvision install``
  (delegates to ``brickvision.cli.install`` for the offline default
  set; this module owns the workspace-bound extensions).

This module deliberately stays import-light; the workspace-bound
SDK adapters live behind narrow interfaces so the offline tier
boots without a Databricks workspace handle.
"""

__all__: list[str] = []
