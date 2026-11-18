#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
MODE="${1:-http}"
if [[ "$MODE" == tls ]]; then
  docker compose -f docker-compose.yml -f deployment/aws/compose.ec2.yml -f deployment/aws/compose.tls.yml build --pull
else
  docker compose -f docker-compose.yml -f deployment/aws/compose.ec2.yml build --pull
fi
bash deployment/aws/deploy.sh "$MODE"
