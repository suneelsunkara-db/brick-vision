"""Schema migrations registry.

Each migration is a SQL file in this folder named
``v<release>_<short_name>.sql``. The install CLI runs them in
lexical order during the `post_deploy.run_migrations` step. Each
file is **idempotent** — repeated runs against an
already-migrated workspace are no-ops.

The migrations themselves are workspace-bound (they require the
partner's catalog) so the in-process tests cover only the SQL
*shape*, not execution.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent

_FILENAME_RE = re.compile(r"^v(?P<release>[0-9_]+)_(?P<name>[a-z0-9_]+)\.sql$")


@dataclasses.dataclass(frozen=True, slots=True)
class Migration:
    release: str
    name: str
    path: Path

    def render(self, *, catalog: str, schema: str = "brickvision") -> str:
        return (
            self.path.read_text()
            .replace("${BV_CATALOG}", catalog)
            .replace("${BV_SCHEMA}", schema)
        )


def list_migrations() -> list[Migration]:
    """Discover every migration in lexical order."""

    out: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _FILENAME_RE.match(path.name)
        if not m:
            continue
        out.append(
            Migration(
                release=m.group("release"),
                name=m.group("name"),
                path=path,
            )
        )
    return out


__all__ = ["Migration", "list_migrations"]
