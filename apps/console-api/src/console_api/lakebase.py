"""Lakebase Postgres connection and query helpers."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

def workspace_client() -> Any:
    """Create a Databricks client without host metadata auto-discovery."""

    from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]

    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "").strip()
    if host and client_id and client_secret:
        return WorkspaceClient(
            host=host,
            client_id=client_id,
            client_secret=client_secret,
        )
    if host and token:
        return WorkspaceClient(host=host, token=token)
    return WorkspaceClient()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def bv_schema() -> str:
    """The UC schema where Synced Tables live (Postgres mirrors this name)."""

    return os.environ.get("BV_SCHEMA", "brickvision")


def lakebase_configured() -> bool:
    """Whether the env has the minimum knobs for a Lakebase read attempt.

    Existing local installs authenticate with ``DATABRICKS_HOST`` +
    ``DATABRICKS_TOKEN`` and infer the Postgres OAuth role from the current
    workspace user. Service-principal installs may set explicit PGUSER /
    BV_LAKEBASE_PRINCIPAL instead.
    """

    database = (
        os.environ.get("PGDATABASE", "").strip()
        or os.environ.get("BV_LAKEBASE_DATABASE", "").strip()
    )
    host = _env_first("PGHOST", "BV_LAKEBASE_HOST")
    workspace_auth = bool(
        os.environ.get("DATABRICKS_HOST", "").strip()
        and (
            os.environ.get("DATABRICKS_TOKEN", "").strip()
            or (
                os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
                and os.environ.get("DATABRICKS_CLIENT_SECRET", "").strip()
            )
        )
    )
    return bool(
        database
        and workspace_auth
        and (
            host
            or (
                os.environ.get("BV_LAKEBASE_PROJECT_ID", "").strip()
                and os.environ.get("BV_LAKEBASE_BRANCH", "").strip()
            )
        )
    )


def lakebase_config_status() -> dict[str, Any]:
    """Return a UI-safe Lakebase configuration status."""

    database = _env_first("PGDATABASE", "BV_LAKEBASE_DATABASE")
    principal = _env_first("PGUSER", "BV_LAKEBASE_PRINCIPAL", "DATABRICKS_CLIENT_ID")
    host = _env_first("PGHOST", "BV_LAKEBASE_HOST")
    endpoint_name = _env_first("ENDPOINT_NAME", "BV_LAKEBASE_ENDPOINT_NAME")
    project_id = os.environ.get("BV_LAKEBASE_PROJECT_ID", "").strip()
    branch = os.environ.get("BV_LAKEBASE_BRANCH", "").strip()
    missing: list[str] = []
    if not database:
        missing.append("PGDATABASE or BV_LAKEBASE_DATABASE")
    if not principal and not os.environ.get("DATABRICKS_TOKEN", "").strip():
        missing.append(
            "PGUSER/BV_LAKEBASE_PRINCIPAL/DATABRICKS_CLIENT_ID, "
            "or DATABRICKS_TOKEN for user-token mode"
        )
    if not host and not endpoint_name and not (project_id and branch):
        missing.append(
            "PGHOST/BV_LAKEBASE_HOST, ENDPOINT_NAME/BV_LAKEBASE_ENDPOINT_NAME, "
            "or BV_LAKEBASE_PROJECT_ID + BV_LAKEBASE_BRANCH"
        )
    configured = not missing
    return {
        "configured": configured,
        "missing": missing,
        "message": (
            "Lakebase direct Postgres config is ready."
            if configured
            else "Lakebase direct Postgres config is incomplete: "
            + "; ".join(missing)
            + "."
        ),
    }


def sanitize_ident(name: str) -> str:
    """Reject anything that isn't a safe Postgres identifier.

    psycopg parameterises *values* but not *identifiers*; the schema +
    table names below are interpolated into the SQL string directly.
    Defence in depth: refuse anything outside ``[A-Za-z0-9_]`` so a
    misconfigured env var can't open an injection vector.
    """

    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


# ---------------------------------------------------------------------------
# Lakebase OAuth + connection
# ---------------------------------------------------------------------------


TOKEN_TTL_S = 50 * 60  # refresh well before the 60-minute Postgres token TTL
POSTGRES_PORT = 5432   # Lakebase Postgres always listens on the standard port
token_cache: dict[str, Any] = {
    "token": None,
    "expires_at": 0.0,
    "principal": None,
}
host_cache: dict[str, str] = {"host": ""}
token_lock = threading.Lock()


def resolve_lakebase_host() -> str:
    """Return the Postgres endpoint hostname for the configured branch.

    Calls ``WorkspaceClient.postgres.list_endpoints`` once, caches
    the result for the process lifetime (the host is stable per-branch
    — it changes only if the branch is destroyed and re-created, in
    which case a sidecar restart picks up the new value).
    """

    explicit = _env_first("PGHOST", "BV_LAKEBASE_HOST")
    if explicit:
        host_cache["host"] = explicit
        return explicit

    cached = host_cache.get("host")
    if cached:
        return cached

    with token_lock:
        cached = host_cache.get("host")
        if cached:
            return cached

        client = workspace_client()
        branch_name = (
            f"projects/{os.environ['BV_LAKEBASE_PROJECT_ID']}"
            f"/branches/{os.environ.get('BV_LAKEBASE_BRANCH', 'production')}"
        )

        endpoints = list(client.postgres.list_endpoints(parent=branch_name))
        host = ""
        for ep in endpoints:
            status = getattr(ep, "status", None)
            if status:
                hosts = getattr(status, "hosts", None)
                if hosts:
                    host = getattr(hosts, "host", "") or ""
                    if host:
                        break

        if not host:
            host = extract_pg_host_fallback(endpoints, branch_name)

        if not host:
            raise RuntimeError(
                "Could not resolve the Lakebase Postgres endpoint hostname "
                f"from list_endpoints({branch_name!r}). Endpoints: {endpoints!r}",
            )

        host_cache["host"] = host
        return host


def extract_pg_host_fallback(endpoints: Any, branch_name: str) -> str:  # noqa: ANN401
    """Best-effort extraction of the Postgres host from endpoint objects.

    Falls back to traversing various SDK attribute paths. Returns
    ``""`` if none resolve to a usable hostname.
    """

    for ep in endpoints:
        for nested_attr in ("status", "connection", "endpoint"):
            section = getattr(ep, nested_attr, None)
            if section is None:
                continue
            host = getattr(section, "host", None)
            if isinstance(host, str) and host.strip():
                return host.strip()
            hosts = getattr(section, "hosts", None)
            if hosts:
                h = getattr(hosts, "host", None)
                if isinstance(h, str) and h.strip():
                    return h.strip()
            url = getattr(section, "url", None)
            if isinstance(url, str) and url.strip():
                return strip_to_host(url.strip())

    return ""


def extract_pg_host(branch: Any) -> str:  # noqa: ANN401
    """Best-effort extraction of a Postgres host from one SDK object."""

    for attr in ("pg_endpoint", "host"):
        value = getattr(branch, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for nested_attr in ("status", "connection", "endpoint"):
        section = getattr(branch, nested_attr, None)
        if section is None:
            continue
        host = getattr(section, "host", None)
        if isinstance(host, str) and host.strip():
            return host.strip()
        hosts = getattr(section, "hosts", None)
        if hosts:
            nested_host = getattr(hosts, "host", None)
            if isinstance(nested_host, str) and nested_host.strip():
                return nested_host.strip()
        url = getattr(section, "url", None)
        if isinstance(url, str) and url.strip():
            return strip_to_host(url.strip())

    return ""


def strip_to_host(url: str) -> str:
    """Reduce a ``postgresql://...@host:5432/db`` URL to just ``host``.

    Some SDK versions return a full DSN where we wanted only the
    hostname. Robust enough to handle the common cases without
    pulling in a URL-parsing dependency.
    """

    after_scheme = url.split("://", 1)[-1]
    after_credentials = after_scheme.rsplit("@", 1)[-1]
    host_with_port = after_credentials.split("/", 1)[0]
    return host_with_port.split(":", 1)[0]


endpoint_name_cache: dict[str, str] = {}


def resolve_lakebase_endpoint_name() -> str:
    """Return the fully-qualified endpoint name for credential generation.

    Format: ``projects/{id}/branches/{branch}/endpoints/{endpoint_id}``.
    Resolves by listing endpoints on the branch and picking the first
    read-write (or only) endpoint. Cached for the process lifetime.
    """

    explicit = _env_first("ENDPOINT_NAME", "BV_LAKEBASE_ENDPOINT_NAME")
    if explicit:
        endpoint_name_cache["name"] = explicit
        return explicit

    cached = endpoint_name_cache.get("name")
    if cached:
        return cached

    client = workspace_client()
    branch_name = (
        f"projects/{os.environ['BV_LAKEBASE_PROJECT_ID']}"
        f"/branches/{os.environ.get('BV_LAKEBASE_BRANCH', 'production')}"
    )
    endpoints = list(client.postgres.list_endpoints(parent=branch_name))
    if not endpoints:
        raise RuntimeError(
            f"No Lakebase endpoints found for branch {branch_name!r}. "
            "Ensure the Lakebase project/branch is provisioned.",
        )

    ep_name = getattr(endpoints[0], "name", "") or ""
    if not ep_name:
        project_id = os.environ["BV_LAKEBASE_PROJECT_ID"]
        branch = os.environ.get("BV_LAKEBASE_BRANCH", "production")
        ep_name = f"projects/{project_id}/branches/{branch}/endpoints/primary"

    endpoint_name_cache["name"] = ep_name
    return ep_name


def lakebase_oauth_credential() -> tuple[str, str]:
    """Return ``(postgres_user, postgres_password)`` for Lakebase.

    Existing local installs infer the Postgres OAuth role from the
    ``DATABRICKS_TOKEN`` identity. Service-principal installs can override it
    with ``PGUSER`` / ``BV_LAKEBASE_PRINCIPAL`` / ``DATABRICKS_CLIENT_ID``.
    The password is a short-lived OAuth bearer minted by
    ``WorkspaceClient.postgres.generate_database_credential``.

    Cached for 50 minutes; refreshes are serialised with a lock so
    concurrent requests don't stampede the token endpoint.
    """

    now = time.time()
    with token_lock:
        cached = token_cache["token"]
        if cached and token_cache["expires_at"] > now and token_cache["principal"]:
            return token_cache["principal"], cached

        principal = resolve_lakebase_principal()
        client = workspace_client()
        endpoint_name = resolve_lakebase_endpoint_name()
        cred = client.postgres.generate_database_credential(
            endpoint=endpoint_name,
        )
        token = getattr(cred, "token", None) or getattr(cred, "credential", None)
        if not token:
            raise RuntimeError(
                "WorkspaceClient.postgres.generate_database_credential returned "
                "no token; cannot authenticate to Lakebase Postgres.",
            )

        token_cache["principal"] = principal
        token_cache["token"] = token
        token_cache["expires_at"] = now + TOKEN_TTL_S
        return principal, token


def resolve_lakebase_principal() -> str:
    """Return the Postgres OAuth role used as ``PGUSER``."""

    principal = _env_first(
        "PGUSER",
        "BV_LAKEBASE_PRINCIPAL",
        "DATABRICKS_CLIENT_ID",
    )
    if principal:
        return principal
    principal = resolve_current_workspace_user()
    if not principal:
        raise RuntimeError(
            "Could not resolve Lakebase Postgres user from DATABRICKS_TOKEN. "
            "Set PGUSER or BV_LAKEBASE_PRINCIPAL only if using an explicit "
            "service-principal OAuth role."
        )
    return principal


def resolve_current_workspace_user() -> str:
    """Resolve the current Databricks user for PAT-backed Lakebase auth."""

    cached = token_cache.get("principal")
    if cached:
        return str(cached)
    payload = databricks_api_get_json("/api/2.0/preview/scim/v2/Me")
    principal = str(
        payload.get("userName")
        or payload.get("user_name")
        or payload.get("displayName")
        or payload.get("display_name")
        or ""
    ).strip()
    if principal:
        token_cache["principal"] = principal
    return principal


def databricks_api_get_json(path: str) -> dict[str, Any]:
    """Small bounded Databricks REST GET used to avoid SDK metadata discovery."""

    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_TOKEN are required.")
    request = Request(
        host + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = f"Databricks API GET {path} failed: HTTP {exc.code}: {body}"
        raise RuntimeError(message) from exc


persistent_conn: Any = None
persistent_conn_lock = threading.Lock()


@contextmanager
def lakebase_connection() -> Iterator[Any]:
    """Yield a persistent psycopg connection to Lakebase Postgres.

    Keeps a single long-lived connection (autocommit, read-only) and
    reuses it across requests to avoid the ~3s TLS handshake per query.
    Reconnects automatically if the connection is closed or broken.
    """

    import psycopg  # type: ignore[import-not-found]

    global persistent_conn  # noqa: PLW0603

    with persistent_conn_lock:
        conn = persistent_conn
        if conn is not None:
            try:
                conn.execute("SELECT 1")
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                persistent_conn = None

        if conn is None:
            user, password = lakebase_oauth_credential()
            host = resolve_lakebase_host()
            conn = psycopg.connect(
                host=host,
                port=int(os.environ.get("PGPORT", POSTGRES_PORT)),
                dbname=(
                    os.environ.get("PGDATABASE", "").strip()
                    or os.environ["BV_LAKEBASE_DATABASE"]
                ),
                user=user,
                password=password,
                sslmode=os.environ.get("PGSSLMODE", "require").strip() or "require",
                autocommit=True,
                connect_timeout=10,
            )
            persistent_conn = conn

    try:
        yield conn
    except Exception:
        with persistent_conn_lock:
            try:
                conn.close()
            except Exception:
                pass
            persistent_conn = None
        raise


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Run a SELECT and return ``cursor.fetchall()``.

    Configuration checks still return an empty read model so the console can
    show an explicit setup state. Runtime Lakebase failures are raised instead
    of being converted into empty data; otherwise the UI hides the real cause.
    """

    if not lakebase_configured():
        return []
    with lakebase_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def query_one(sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    """Run a SELECT and return ``cursor.fetchone()`` or ``None``."""

    if not lakebase_configured():
        return None
    with lakebase_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""



