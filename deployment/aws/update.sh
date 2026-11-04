#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
MODE="${1:-http}"
if [[ "$MODE" == tls ]]; then
  docker compose --profile tls build --pull
else
  docker compose build --pull
fi
bash deployment/aws/deploy.sh "$MODE"
