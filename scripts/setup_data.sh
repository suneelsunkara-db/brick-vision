#!/usr/bin/env bash
# Prepare BrickVision's Databricks data plane and trigger the serverless
# capability indexer Job. This is the setup/data command; it does not start
# or deploy any UI.
#
# Usage:
#   bash scripts/setup_data.sh
#   bash scripts/setup_data.sh --doctor
#   bash scripts/setup_data.sh --skip-indexer-deploy
#   bash scripts/setup_data.sh --refresh-only

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

LOG_FILE="${BV_LOCAL_DEPLOY_LOG_PATH:-${REPO_ROOT}/local_deploy.log}"
PYTHON="${PYTHON:-python3}"

if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  BLUE=$'\033[34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BLUE=""; BOLD=""; RESET=""
fi

log_step() { printf '%s\n' "${BOLD}${BLUE}── $* ${RESET}" | tee -a "${LOG_FILE}" >&2; }
log_ok()   { printf '%s\n' " ${GREEN}✓${RESET}  $*" | tee -a "${LOG_FILE}" >&2; }
log_warn() { printf '%s\n' " ${YELLOW}⚠${RESET}  $*" | tee -a "${LOG_FILE}" >&2; }
log_fail() { printf '%s\n' " ${RED}✗${RESET}  $*" | tee -a "${LOG_FILE}" >&2; }
log_info() { printf '%s\n' " ·  $*" | tee -a "${LOG_FILE}" >&2; }

DOCTOR_MODE=false
RUN_PROVISION=true
RUN_INSTALL=true
DEPLOY_INDEXER=true
TRIGGER_REFRESH=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --doctor) DOCTOR_MODE=true; shift ;;
    --skip-provision) RUN_PROVISION=false; shift ;;
    --skip-install) RUN_INSTALL=false; shift ;;
    --skip-indexer-deploy) DEPLOY_INDEXER=false; shift ;;
    --no-refresh) TRIGGER_REFRESH=false; shift ;;
    --refresh-only)
      RUN_PROVISION=false
      RUN_INSTALL=false
      DEPLOY_INDEXER=false
      TRIGGER_REFRESH=true
      shift
      ;;
    --help|-h)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      log_fail "unknown argument: $1"
      exit 1
      ;;
  esac
done

log_step "Setup data — preflight"
log_info "log file: ${LOG_FILE}"

if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  log_fail "${PYTHON} not found on PATH"
  exit 1
fi
if ! command -v databricks >/dev/null 2>&1; then
  log_fail "databricks CLI not found on PATH"
  exit 1
fi
if [[ ! -f .env ]]; then
  log_fail ".env not found"
  log_info "copy .env.example to .env and fill in Databricks settings"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ -z "${DATABRICKS_HOST:-}" || -z "${DATABRICKS_TOKEN:-}" ]]; then
  log_fail "DATABRICKS_HOST and DATABRICKS_TOKEN must be set in .env"
  exit 1
fi

log_info "DATABRICKS_HOST=${DATABRICKS_HOST}"
log_info "BV_CATALOG=${BV_CATALOG:-brickvision}"
log_info "BV_SCHEMA=${BV_SCHEMA:-brickvision}"
log_info "BV_VS_ENDPOINT=${BV_VS_ENDPOINT:-brickvision-dev}"
log_ok "preflight complete"

if [[ "${DOCTOR_MODE}" == true ]]; then
  log_step "doctor mode — read-only diagnostics"
  exec "${PYTHON}" -m scripts.local_deploy.provision_workspace --doctor
fi

if [[ "${BV_DRY_RUN:-false}" =~ ^(1|true|yes|on)$ ]]; then
  log_warn "BV_DRY_RUN=true — setup_data.sh will not write workspace data"
  exec "${PYTHON}" -m scripts.local_deploy.provision_workspace --doctor
fi

if [[ "${RUN_PROVISION}" == true ]]; then
  log_step "Setup data — provision UC, grants, Vector Search"
  if ! "${PYTHON}" -m scripts.local_deploy.provision_workspace; then
    log_fail "workspace provisioning failed"
    exit 1
  fi
  log_ok "workspace substrate ready"
else
  log_warn "workspace provisioning skipped"
fi

if [[ "${RUN_INSTALL}" == true ]]; then
  log_step "Setup data — install preflights"
  if ! "${PYTHON}" -m brickvision.cli install; then
    log_fail "install preflights failed"
    exit 1
  fi
  log_ok "install preflights passed"
else
  log_warn "install preflights skipped"
fi

if [[ "${DEPLOY_INDEXER}" == true ]]; then
  log_step "Setup data — create/update serverless capability indexer Job"
  if ! "${PYTHON}" -m scripts.local_deploy.deploy_indexer_job; then
    log_fail "indexer Job deploy failed"
    exit 1
  fi
  log_ok "indexer Job deployed"
else
  log_warn "indexer Job create/update skipped; setup will trigger the existing serverless Job"
fi

if [[ "${TRIGGER_REFRESH}" == true ]]; then
  log_step "Setup data — trigger serverless indexer refresh"
  if ! "${PYTHON}" -m brickvision.cli indexer refresh; then
    log_fail "indexer refresh trigger failed"
    exit 1
  fi
  log_ok "indexer refresh triggered"
else
  log_warn "indexer refresh skipped"
fi

log_step "Setup data — done"
cat <<EOF | tee -a "${LOG_FILE}" >&2

  ${BOLD}Data setup path complete.${RESET}

  Catalog:      ${BV_CATALOG:-brickvision}
  Schema:       ${BV_SCHEMA:-brickvision}
  VS endpoint:  ${BV_VS_ENDPOINT:-brickvision-dev}
  Indexer Job:  ${BV_INDEXER_JOB_NAME:-bv_capability_indexer}

  Start the local app with:

    bash scripts/start_local_app.sh

  Re-run data refresh without provisioning/deploying:

    bash scripts/setup_data.sh --refresh-only

EOF

