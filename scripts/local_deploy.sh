#!/usr/bin/env bash
# scripts/local_deploy.sh — One-shot, idempotent local deploy of
# BrickVision v0.7.7. Provisions the workspace-side prerequisites for
# capability-graph indexing while keeping the SPA + FastAPI sidecar
# running locally on this machine. Topology + design discipline live
# in scripts/local_deploy/README.md.
#
# Phases (each is idempotent — safe to re-run after partial failures):
#   0. preflight      — verify CLIs, .env, auth
#   1. provision      — SPs + UC catalog/schema + indexer-state Volume
#                       + budget_namespaces + grants + VS endpoint + 3 indexes
#   2. install        — `brickvision install` (8 pre-flights, including
#                       capability-graph N180 probes)
#   3. deploy         — deploy BrickVision-managed serverless Jobs
#                       (capability indexer, workspace KG, evaluation;
#                       no Apps)
#   4. trigger        — `brickvision indexer refresh` first run
#   5. summary        — print operator next steps for local SPA startup
#
# Usage:
#   bash scripts/local_deploy.sh                # full run
#   bash scripts/local_deploy.sh --doctor       # read-only checklist
#   bash scripts/local_deploy.sh --skip vs      # skip VS phase
#   bash scripts/local_deploy.sh --no-trigger   # provision + deploy, skip first refresh
#   bash scripts/local_deploy.sh --resume       # skip already-done phases (idempotent)

set -euo pipefail
# `lastpipe` makes the rightmost command of a pipeline run in the
# current shell so it can set parent-scope variables. We don't depend
# on this (no `... | read VAR` patterns below) but we still try to
# enable it on bash >= 4.2 for forward-compat. macOS ships bash 3.2
# where the option doesn't exist; ignore the failure.
shopt -s lastpipe 2>/dev/null || true

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

LOG_FILE="${BV_LOCAL_DEPLOY_LOG_PATH:-${REPO_ROOT}/local_deploy.log}"

# ---------------------------------------------------------------------------
# Coloured logging
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  BLUE=$'\033[34m'; BOLD=$'\033[1m';   RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BLUE=""; BOLD=""; RESET=""
fi

log_step() {
  printf '%s\n' "${BOLD}${BLUE}── $* ${RESET}" | tee -a "${LOG_FILE}" >&2
}
log_ok() {
  printf '%s\n' " ${GREEN}✓${RESET}  $*" | tee -a "${LOG_FILE}" >&2
}
log_warn() {
  printf '%s\n' " ${YELLOW}⚠${RESET}  $*" | tee -a "${LOG_FILE}" >&2
}
log_fail() {
  printf '%s\n' " ${RED}✗${RESET}  $*" | tee -a "${LOG_FILE}" >&2
}
log_info() {
  printf '%s\n' " ·  $*" | tee -a "${LOG_FILE}" >&2
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

DOCTOR_MODE=false
TRIGGER_REFRESH=true
RESUME=false
SKIP_PHASES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --doctor)        DOCTOR_MODE=true; shift ;;
    --no-trigger)    TRIGGER_REFRESH=false; shift ;;
    --resume)        RESUME=true; shift ;;
    --skip)
      [[ $# -lt 2 ]] && { log_fail "--skip requires an argument"; exit 1; }
      SKIP_PHASES+=("$2")
      shift 2
      ;;
    --help|-h)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *)
      log_fail "unknown argument: $1"
      exit 1
      ;;
  esac
done

skip_has() {
  local needle="$1"
  for phase in "${SKIP_PHASES[@]:-}"; do
    [[ "${phase}" == "${needle}" ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# Phase 0 — Preflight
# ---------------------------------------------------------------------------

log_step "Phase 0 — preflight"
log_info "log file: ${LOG_FILE}"

require_bin() {
  local bin="$1" minver="${2:-}"
  if ! command -v "${bin}" >/dev/null 2>&1; then
    log_fail "${bin} not found on PATH"
    log_info "  install hint: ${3:-}"
    exit 1
  fi
  log_ok "${bin} on PATH"
}

require_bin python3   "" "Install Python 3.11+ (https://www.python.org/downloads/)"
require_bin databricks "" "Install via 'curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh'"

PYTHON=python3

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    log_warn ".env not found — bootstrapping from .env.example"
    log_warn "  ► open .env, fill in DATABRICKS_HOST + DATABRICKS_TOKEN, re-run this script"
    cp .env.example .env
    exit 1
  fi
  log_fail ".env not found and no .env.example to seed from"
  exit 1
fi
log_ok ".env present"

# Source .env so DATABRICKS_HOST / DATABRICKS_TOKEN are available to the
# `databricks` CLI invoked later. The Python helpers re-load via load_dotenv()
# so this is purely for shell-level subcommands.
set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ -z "${DATABRICKS_HOST:-}" || -z "${DATABRICKS_TOKEN:-}" ]]; then
  log_fail "DATABRICKS_HOST and DATABRICKS_TOKEN must be set in .env"
  exit 1
fi

log_info "DATABRICKS_HOST=${DATABRICKS_HOST}"
log_info "BV_CATALOG=${BV_CATALOG:-brickvision_dev}"
log_info "BV_VS_ENDPOINT=${BV_VS_ENDPOINT:-brickvision-dev}"
log_ok "phase 0 complete"

# ---------------------------------------------------------------------------
# Doctor mode short-circuit
# ---------------------------------------------------------------------------

if [[ "${DOCTOR_MODE}" == true ]]; then
  log_step "doctor mode — read-only diagnostics"
  ${PYTHON} -m scripts.local_deploy.provision_workspace --doctor
  exit $?
fi

if [[ "${BV_DRY_RUN:-false}" =~ ^(1|true|yes|on)$ ]]; then
  log_warn "BV_DRY_RUN=true — local_deploy.sh will not perform a real end-to-end run"
  log_info "  running read-only doctor checks, then exiting before provision/deploy/trigger"
  log_info "  set BV_DRY_RUN=false in .env when you want the real indexer run"
  ${PYTHON} -m scripts.local_deploy.provision_workspace --doctor
  exit $?
fi

# ---------------------------------------------------------------------------
# Phase 1 — Provision workspace
# ---------------------------------------------------------------------------

log_step "Phase 1 — provision workspace (SPs + UC + VS)"

PROV_ARGS=()
for phase in "${SKIP_PHASES[@]:-}"; do
  case "${phase}" in
    sp|whse|uc|ddl|budget|grant|vs)
      PROV_ARGS+=(--skip "${phase}")
      ;;
  esac
done

if [[ ${#PROV_ARGS[@]} -gt 0 ]]; then
  if ! ${PYTHON} -m scripts.local_deploy.provision_workspace "${PROV_ARGS[@]}"; then
    log_fail "provision_workspace failed — re-run \`bash scripts/local_deploy.sh --resume\` to retry idempotently"
    exit 1
  fi
elif ! ${PYTHON} -m scripts.local_deploy.provision_workspace; then
  log_fail "provision_workspace failed — re-run \`bash scripts/local_deploy.sh --resume\` to retry idempotently"
  exit 1
fi
log_ok "phase 1 complete"

# ---------------------------------------------------------------------------
# Phase 2 — `brickvision install`
# ---------------------------------------------------------------------------

if skip_has install; then
  log_warn "Phase 2 — skipped (--skip install)"
else
  log_step "Phase 2 — brickvision install (pre-flights)"
  if ! ${PYTHON} -m brickvision.cli install; then
    log_fail "brickvision install failed — inspect the failures above"
    log_info "  re-run a single check via:"
    log_info "    python3 -m brickvision.cli install"
    exit 1
  fi
  log_ok "phase 2 complete"
fi

# ---------------------------------------------------------------------------
# Phase 3 — Deploy managed Jobs (slim DAB)
# ---------------------------------------------------------------------------

if skip_has deploy; then
  log_warn "Phase 3 — skipped (--skip deploy)"
else
  log_step "Phase 3 — deploy managed Jobs (indexer, workspace KG, evaluation; no Apps)"
  if ! ${PYTHON} -m scripts.local_deploy.deploy_indexer_job; then
    log_fail "deploy_indexer_job failed"
    exit 1
  fi
  log_ok "phase 3 complete"
fi

# ---------------------------------------------------------------------------
# Phase 4 — Trigger first refresh
# ---------------------------------------------------------------------------

if [[ "${TRIGGER_REFRESH}" == false ]] || skip_has trigger; then
  log_warn "Phase 4 — skipped (--no-trigger or --skip trigger)"
else
  log_step "Phase 4 — trigger first indexer refresh"
  log_info "  this can take 15-30 min on first run (cold FMS, VS sync)"
  if ! ${PYTHON} -m brickvision.cli indexer refresh; then
    log_fail "indexer refresh trigger failed — inspect Job page in workspace"
    log_info "  Job: bv_capability_indexer (https://${DATABRICKS_HOST}/jobs)"
    exit 1
  fi
  log_ok "phase 4 complete"
fi

# ---------------------------------------------------------------------------
# Phase 5 — Operator summary
# ---------------------------------------------------------------------------

log_step "Phase 5 — done; next steps"

cat <<EOF | tee -a "${LOG_FILE}" >&2

  ${BOLD}Workspace ready.${RESET}

  Catalog:        ${BV_CATALOG:-brickvision_dev}
  VS endpoint:    ${BV_VS_ENDPOINT:-brickvision-dev}
  Indexer Job:    bv_capability_indexer
  Evaluation Job: bv_evaluation_scorers
  Job page:       ${DATABRICKS_HOST}/jobs

  ${BOLD}To start the local SPA + FastAPI sidecar:${RESET}

    bash scripts/local_deploy/start_local_spa.sh

  Once the sidecar is up:
    open http://localhost:5173/               # redirects to /knowledge
    open http://localhost:5173/knowledge      # Knowledge UI (Top-Orders, Meta-Skills, Extensions, Provenance, Health)

  ${BOLD}To re-trigger the indexer:${RESET}

    python3 -m brickvision.cli indexer refresh

  ${BOLD}To monitor the current refresh:${RESET}

    python3 -m brickvision.cli indexer status

  ${BOLD}Troubleshooting:${RESET}

    bash scripts/local_deploy.sh --doctor      # read-only checklist
    cat ${LOG_FILE}                            # full execution log

EOF

log_ok "local_deploy.sh completed successfully"
