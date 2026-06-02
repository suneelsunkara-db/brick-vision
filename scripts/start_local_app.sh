#!/usr/bin/env bash
# Start the local BrickVision app only: FastAPI sidecar + Vite SPA.
# Data setup lives in scripts/setup_data.sh.

set -euo pipefail

cd "$(dirname "$0")/.."
exec bash scripts/local_deploy/start_local_spa.sh "$@"

