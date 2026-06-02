#!/usr/bin/env bash
# scripts/local_deploy/start_local_spa.sh — Run the BrickVision SPA +
# FastAPI sidecar on this machine, talking to the workspace
# bootstrapped by ``scripts/local_deploy.sh``.
#
# Why a separate script: the workspace bootstrap is one-shot (run
# once, then rerun the indexer); the local SPA + sidecar is long-lived
# while you click through the /knowledge UI. Splitting them lets you
# re-run either independently.
#
# What this starts:
#   - FastAPI sidecar  on  :8000   (apps/console-api/src/console_api/main.py)
#   - Vite dev server  on  :5173   (apps/console)
#
# Both run in the foreground attached to your shell. Ctrl-C stops both.
#
# Required env (sourced from .env):
#   DATABRICKS_HOST, DATABRICKS_TOKEN
#   BV_CATALOG, BV_VS_ENDPOINT
# Optional env:
#   BV_LOCAL_SPA_PORT       (default 5173)
#   BV_LOCAL_SIDECAR_PORT   (default 8000)
#   BV_DRY_RUN              (set to "true" to render with fixture data only)
#   BV_LOCAL_SETUP_SWITCH   (default true; install/seed Lakebridge Switch test inputs)
#   BV_CODE_CONVERT_VOLUME  (default BV_INDEXER_STATE_VOLUME)
#
# Usage:
#   bash scripts/local_deploy/start_local_spa.sh
#   bash scripts/local_deploy/start_local_spa.sh --check

set -euo pipefail

cd "$(dirname "$0")/../.."
REPO_ROOT="$(pwd)"

# Prefer the repo-local virtualenv when present so users can run this script
# directly without activating .venv or manually prefixing PATH.
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  export PATH="${REPO_ROOT}/.venv/bin:${PATH}"
fi

# ----------------------------------------------------------------- colours
if [[ -t 1 ]]; then
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'
  BLUE=$'\033[34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  GREEN=""; YELLOW=""; RED=""; BLUE=""; BOLD=""; RESET=""
fi
log()  { printf '%s\n' "${BOLD}${BLUE}── $* ${RESET}" >&2; }
ok()   { printf '%s\n' " ${GREEN}✓${RESET}  $*" >&2; }
warn() { printf '%s\n' " ${YELLOW}⚠${RESET}  $*" >&2; }
fail() { printf '%s\n' " ${RED}✗${RESET}  $*" >&2; }

CHECK_ONLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=true; shift ;;
    --help|-h)
      sed -n '2,28p' "$0"
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      exit 1
      ;;
  esac
done

# ----------------------------------------------------------------- preflight
log "preflight"

if [[ ! -f .env ]]; then
  fail ".env not found — run \`bash scripts/setup_data.sh\` first"
  exit 1
fi
set -a; source .env; set +a

if [[ -z "${DATABRICKS_HOST:-}" || -z "${DATABRICKS_TOKEN:-}" ]]; then
  fail "DATABRICKS_HOST + DATABRICKS_TOKEN must be set in .env"
  exit 1
fi
ok ".env loaded"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi-proxy.cloud.databricks.com/simple}"
export UV_INDEX_URL="${UV_INDEX_URL:-${PIP_INDEX_URL}}"
export LLM_GENERAL_TASKS="${LLM_GENERAL_TASKS:-databricks-qwen3-next-80b-a3b-instruct}"
export LLM_EMBEDDING_TASKS="${LLM_EMBEDDING_TASKS:-databricks-qwen3-embedding-0-6b}"

command -v uvicorn >/dev/null 2>&1 || {
  fail "uvicorn not found — \`pip install uvicorn 'fastapi'\` or \`uv pip install -e apps/console-api\`"
  exit 1
}
command -v pnpm >/dev/null 2>&1 || command -v npm >/dev/null 2>&1 || {
  fail "neither pnpm nor npm found — install Node 20+ (https://nodejs.org/)"
  exit 1
}
ok "uvicorn + node toolchain present"

if [[ ! -d apps/console/node_modules ]]; then
  warn "apps/console/node_modules missing — running install (one-time, ~30s)"
  if command -v pnpm >/dev/null 2>&1; then
    (cd apps/console && pnpm install --frozen-lockfile)
  else
    (cd apps/console && npm install --no-audit --no-fund)
  fi
fi

# ---------------------------------------------------------- Lakebridge Switch
CODE_CONVERT_VOLUME="${BV_CODE_CONVERT_VOLUME:-${BV_INDEXER_STATE_VOLUME:-indexer-state}}"
export BV_CODE_CONVERT_VOLUME="${CODE_CONVERT_VOLUME}"
export BV_CODE_CONVERT_SOURCE_PATH="${BV_CODE_CONVERT_SOURCE_PATH:-/Volumes/${BV_CATALOG:-brickvision}/${BV_SCHEMA:-brickvision}/${CODE_CONVERT_VOLUME}/lakebridge/pyspark/source}"
export BV_CODE_CONVERT_OUTPUT_PATH="${BV_CODE_CONVERT_OUTPUT_PATH:-/Volumes/${BV_CATALOG:-brickvision}/${BV_SCHEMA:-brickvision}/${CODE_CONVERT_VOLUME}/lakebridge/pyspark/output}"
export BV_SWITCH_MODEL_ENDPOINT="${BV_SWITCH_MODEL_ENDPOINT:-${LLM_GENERAL_TASKS}}"
export BV_SQL_TRANSPILE_SOURCE_PATH="${BV_SQL_TRANSPILE_SOURCE_PATH:-/Volumes/${BV_CATALOG:-brickvision}/${BV_SCHEMA:-brickvision}/${CODE_CONVERT_VOLUME}/lakebridge/sql/source}"
export BV_SQL_TRANSPILE_OUTPUT_PATH="${BV_SQL_TRANSPILE_OUTPUT_PATH:-/Volumes/${BV_CATALOG:-brickvision}/${BV_SCHEMA:-brickvision}/${CODE_CONVERT_VOLUME}/lakebridge/sql/output}"
export BV_SQL_TRANSPILE_SOURCE_FILENAME="${BV_SQL_TRANSPILE_SOURCE_FILENAME:-teradata_spend_customer_rollup.sql}"
export BV_SQL_TRANSPILE_SOURCE_FILE="${BV_SQL_TRANSPILE_SOURCE_FILE:-${BV_SQL_TRANSPILE_SOURCE_PATH}/${BV_SQL_TRANSPILE_SOURCE_FILENAME}}"
SQL_TRANSPILE_SOURCE_PATH="${BV_SQL_TRANSPILE_SOURCE_PATH}"
SQL_TRANSPILE_OUTPUT_PATH="${BV_SQL_TRANSPILE_OUTPUT_PATH}"
DATABRICKS_CLI_CWD="${TMPDIR:-/tmp}"

workspace_user_name() {
  local payload
  payload="$(cd "${DATABRICKS_CLI_CWD}" && databricks current-user me -o json 2>/dev/null || true)"
  python3 -c 'import json, sys; print((json.loads(sys.argv[1]).get("userName") or "") if len(sys.argv) > 1 and sys.argv[1] else "")' "${payload}" 2>/dev/null || true
}

workspace_home_path() {
  local user_name
  user_name="$(workspace_user_name)"
  if [[ -n "${user_name}" ]]; then
    printf '/Workspace/Users/%s' "${user_name}"
  else
    printf '/Workspace/Users/%s' "${DATABRICKS_USER:-${USER:-brickvision}}"
  fi
}

WORKSPACE_HOME="${BV_CODE_CONVERT_WORKSPACE_HOME:-$(workspace_home_path)}"
export BV_CODE_CONVERT_WORKSPACE_OUTPUT_PATH="${BV_CODE_CONVERT_WORKSPACE_OUTPUT_PATH:-${WORKSPACE_HOME}/lakebridge/pyspark/local/output}"
export BV_CODE_CONVERT_WORKSPACE_USER="${BV_CODE_CONVERT_WORKSPACE_USER:-${WORKSPACE_HOME#/Workspace/Users/}}"

switch_import_ok() {
  python3 - <<'PY' >/dev/null 2>&1
import json
import sys

try:
    import importlib.util
    found = importlib.util.find_spec("databricks.labs.switch") is not None
except ModuleNotFoundError:
    found = False
raise SystemExit(0 if found else 1)
PY
}

lakebridge_cli_ok() {
  local installed
  installed="$(cd "${DATABRICKS_CLI_CWD}" && databricks labs installed 2>/dev/null || true)"
  [[ "${installed}" == *"lakebridge"* ]]
}

lakebridge_switch_cli_ok() {
  local help
  help="$(cd "${DATABRICKS_CLI_CWD}" && databricks labs lakebridge llm-transpile --help 2>&1 || true)"
  [[ "${help}" == *"llm-transpile"* && "${help}" != *"Usage:"$'\n'"  databricks labs [command]"* && "${help}" != *"unknown flag"* ]]
}

seed_switch_source_volume() {
  local tmpdir sample source_uri output_uri
  tmpdir="$(mktemp -d)"
  sample="${tmpdir}/legacy_customer_rollup.py"
  source_uri="dbfs:${BV_CODE_CONVERT_SOURCE_PATH}"
  output_uri="dbfs:${BV_CODE_CONVERT_OUTPUT_PATH}"
  cat > "${sample}" <<'PY'
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, count, sum as sum_


spark = SparkSession.builder.appName("legacy-customer-rollup").getOrCreate()

transactions = spark.table("legacy_finance.transactions")
customers = spark.table("legacy_finance.customers")

active_customers = customers.where(col("status") == "ACTIVE")
monthly_spend = (
    transactions.join(active_customers, "customer_id")
    .groupBy("customer_id")
    .agg(
        sum_("amount").alias("monthly_spend_amount"),
        count("*").alias("transaction_count"),
    )
)

monthly_spend.write.mode("overwrite").saveAsTable("legacy_finance.customer_monthly_spend")
PY
  (cd "${DATABRICKS_CLI_CWD}" && databricks fs mkdirs "${source_uri}") >/dev/null 2>&1
  (cd "${DATABRICKS_CLI_CWD}" && databricks fs mkdirs "${output_uri}") >/dev/null 2>&1
  (cd "${DATABRICKS_CLI_CWD}" && databricks fs cp "${sample}" "${source_uri}/legacy_customer_rollup.py" --overwrite) >/dev/null 2>&1
  rm -rf "${tmpdir}"
}

seed_sql_transpile_source_volume() {
  local sample source_uri output_uri
  sample="${REPO_ROOT}/proof-artifacts/lakebridge/source/teradata_spend_customer_rollup.sql"
  source_uri="dbfs:${SQL_TRANSPILE_SOURCE_PATH}"
  output_uri="dbfs:${SQL_TRANSPILE_OUTPUT_PATH}"
  if [[ ! -f "${sample}" ]]; then
    warn "Lakebridge SQL sample missing at ${sample}"
    return 1
  fi
  (cd "${DATABRICKS_CLI_CWD}" && databricks fs mkdirs "${source_uri}") >/dev/null 2>&1
  (cd "${DATABRICKS_CLI_CWD}" && databricks fs mkdirs "${output_uri}") >/dev/null 2>&1
  (cd "${DATABRICKS_CLI_CWD}" && databricks fs cp "${sample}" "${source_uri}/teradata_spend_customer_rollup.sql" --overwrite) >/dev/null 2>&1
}

if [[ "${CHECK_ONLY}" != true && "${BV_LOCAL_SETUP_SWITCH:-true}" == "true" ]]; then
  log "lakebridge switch local test setup"
  if ! command -v databricks >/dev/null 2>&1; then
    warn "databricks CLI not found; Code Convert will remain blocked on local setup"
  else
    if ! lakebridge_cli_ok; then
      warn "Lakebridge Labs project missing — installing lakebridge"
      if (cd "${DATABRICKS_CLI_CWD}" && databricks labs install lakebridge); then
        ok "Lakebridge Labs project installed"
      else
        warn "Lakebridge Labs install failed; Code Convert will show switch_package blocker"
      fi
    fi

    if switch_import_ok || (lakebridge_cli_ok && lakebridge_switch_cli_ok); then
      ok "Lakebridge Switch available"
    else
      warn "databricks-switch-plugin missing — installing Lakebridge Switch transpiler"
      if (cd "${DATABRICKS_CLI_CWD}" && databricks labs lakebridge install-transpile --include-llm-transpiler true --interactive=false); then
        ok "Lakebridge Switch install command completed"
      else
        warn "Lakebridge Switch install failed; Code Convert will show switch_package blocker"
      fi
    fi

    if seed_switch_source_volume; then
      ok "seeded PySpark source in ${BV_CODE_CONVERT_SOURCE_PATH}"
      ok "prepared converted output Volume path ${BV_CODE_CONVERT_OUTPUT_PATH}"
    else
      warn "could not seed PySpark source into UC Volume; verify READ_VOLUME/WRITE_VOLUME permissions"
    fi

    if seed_sql_transpile_source_volume; then
      ok "seeded SQL Transpile source in ${SQL_TRANSPILE_SOURCE_PATH}"
      ok "prepared SQL Transpile output Volume path ${SQL_TRANSPILE_OUTPUT_PATH}"
    else
      warn "could not seed SQL Transpile source into UC Volume; verify READ_VOLUME/WRITE_VOLUME permissions"
    fi
  fi
fi

# ----------------------------------------------------------------- ports
SPA_PORT="${BV_LOCAL_SPA_PORT:-5173}"
SIDECAR_PORT="${BV_LOCAL_SIDECAR_PORT:-8000}"

port_pids() {
  local pids
  pids="$(lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true)"
  printf '%s' "${pids}" | tr '\n' ' ' | sed 's/[[:space:]]*$//'
}

http_ok() {
  python - "$1" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        raise SystemExit(0 if 200 <= response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
}

SIDECAR_PIDS="$(port_pids "${SIDECAR_PORT}")"
SPA_PIDS="$(port_pids "${SPA_PORT}")"
if [[ -n "${SIDECAR_PIDS}" || -n "${SPA_PIDS}" ]]; then
  if [[ -n "${SIDECAR_PIDS}" && -n "${SPA_PIDS}" ]] \
    && http_ok "http://127.0.0.1:${SIDECAR_PORT}/api/health" \
    && http_ok "http://127.0.0.1:${SPA_PORT}/"; then
    ok "local app already running on ports ${SIDECAR_PORT} (sidecar) + ${SPA_PORT} (SPA)"
    log "  open ${BOLD}http://localhost:${SPA_PORT}/knowledge${RESET}  (real workspace data)"
    exit 0
  fi

  [[ -n "${SIDECAR_PIDS}" ]] && fail "port ${SIDECAR_PORT} already in use by PID(s): ${SIDECAR_PIDS}"
  [[ -n "${SPA_PIDS}" ]] && fail "port ${SPA_PORT} already in use by PID(s): ${SPA_PIDS}"
  fail "stop the existing process(es), or set BV_LOCAL_SIDECAR_PORT / BV_LOCAL_SPA_PORT to free ports"
  exit 1
fi
ok "ports ${SIDECAR_PORT} (sidecar) + ${SPA_PORT} (SPA) free"

if [[ "${CHECK_ONLY}" == true ]]; then
  ok "local app check complete"
  exit 0
fi

# ----------------------------------------------------------------- start
log "starting FastAPI sidecar on :${SIDECAR_PORT}"

# Tell the SPA dev server where the sidecar lives — Vite's proxy reads
# this via VITE_API_BASE.
export VITE_API_BASE="http://localhost:${SIDECAR_PORT}"
export CONSOLE_API_CORS_ALLOW_ORIGINS="http://localhost:${SPA_PORT}"

# Reaper — kill the sidecar when the SPA exits (or on Ctrl-C).
SIDECAR_PID=""
cleanup() {
  [[ -n "${SIDECAR_PID}" ]] && kill "${SIDECAR_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd apps/console-api
  exec uvicorn console_api.main:app \
    --host 127.0.0.1 \
    --port "${SIDECAR_PORT}" \
    --reload
) &
SIDECAR_PID=$!
ok "sidecar PID=${SIDECAR_PID}"

# Give uvicorn a beat to bind so the SPA's first XHR doesn't 502.
sleep 2
if ! kill -0 "${SIDECAR_PID}" 2>/dev/null; then
  fail "sidecar failed to start — see uvicorn output above"
  exit 1
fi

log "starting Vite SPA on :${SPA_PORT}"
log "  open ${BOLD}http://localhost:${SPA_PORT}/knowledge${RESET}  (real workspace data)"
log "  press Ctrl-C to stop both servers"

cd apps/console
if command -v pnpm >/dev/null 2>&1; then
  exec pnpm dev --host 127.0.0.1 --port "${SPA_PORT}"
else
  exec npm run dev -- --host 127.0.0.1 --port "${SPA_PORT}"
fi
