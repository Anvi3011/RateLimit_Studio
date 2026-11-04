#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
URL="${HEALTHCHECK_URL:-http://localhost:80/api/health}"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 "$URL"; then echo; exit 0; fi
  sleep 2
done
echo "Application health check did not pass: $URL" >&2
docker compose ps >&2 || true
docker compose logs --tail=100 backend publisher worker nginx nginx-tls >&2 || true
exit 1
