"""brickvision-runtime — the harness-side library.

Per `docs/18-architecture.md` §14, this package is the runtime substrate for
generated harnesses. It must NEVER `import brickvision` (the build-time
package). The CI `import_scan.yaml` job enforces this at the wire.
"""

__version__ = "0.1.0"
