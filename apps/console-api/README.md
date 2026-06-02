# `console-api` — partner-side Console FastAPI sidecar

> Status: v0.7.6.9 · scaffolded under N52.0' / N52.0″ · paired with
> [`apps/console`](../console) (Vite + React SPA).

## Why this exists

The partner-side Visual Builder is a **first-class React SPA**
(`apps/console`). Every interaction the SPA makes against the
partner's Databricks workspace — listing builds, fetching IR,
streaming build events, signing/approving runs, listing skills,
peeking at KG rows — flows through this **FastAPI sidecar** so that
all OBO-token handling, signature verification, and audit emission
stay strictly server-side.

This is the **only** approved transport between the SPA and
`brickvision-runtime`. The SPA must never call the Databricks SDK
directly. See:

- `docs/12-visual-builder.md` §10.2 + §10.7.7.B (architecture)
- `docs/16-identity-audit-replay.md` §12.4 (ephemeral allowlist)
- `docs/08-transpiler.md` §7.8.E.1 + §7.8.E.7 (templates that emit a
  twin sidecar for the End-Customer Console)

## Local development

```bash
# from the repo root, with uv installed
uv venv apps/console-api/.venv
source apps/console-api/.venv/bin/activate
uv pip install -e "apps/console-api[dev]"

# ASGI entry point — pick up the partner workspace via
# DATABRICKS_HOST + DATABRICKS_TOKEN (or `databricks auth login`)
uvicorn console_api.main:app --host 127.0.0.1 --port 8000 --reload
```

The Vite dev server proxies `/api/*` and `/ws/*` to `localhost:8000`
(see `apps/console/vite.config.ts`).

## Endpoint surface (v0.6 / Phase 6 floor)

| Method | Path                                | Purpose                                |
|--------|-------------------------------------|----------------------------------------|
| GET    | `/api/health`                       | liveness + version                     |
| GET    | `/api/me`                           | current OBO identity (read from header) |
| GET    | `/api/builds`                       | recent build runs                      |
| GET    | `/api/builds/{build_id}`            | single build run                       |
| GET    | `/api/builds/{build_id}/ir`         | DesignArtifact (IR) JSON               |
| GET    | `/api/builds/{build_id}/findings`   | validation findings                    |
| GET    | `/api/skills`                       | catalog                                |
| WS     | `/api/builds/{build_id}/stream`     | live BuildEvent stream                 |

Every route enforces:

1. OBO token present and unexpired (else `401 ApiError` with
   `reason_code = "OBO_TOKEN_EXPIRED"`).
2. SPA-bound JSON response only — never the raw OBO token.
3. Audit row written via `brickvision_runtime.audit` for any
   non-GET action.
