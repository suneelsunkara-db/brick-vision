"""FastAPI routers for the Console API (v0.7.7 MVI).

- ``health``    — liveness + version
- ``identity``  — current OBO identity
- ``knowledge`` — Capability Catalog reads (Top-Orders / Meta-Skills /
                  Extensions / Refresh history / Provenance)
"""

from . import health, identity, knowledge

__all__ = ["health", "identity", "knowledge"]
