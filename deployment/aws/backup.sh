#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
DEST="${1:-./backups/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
chmod 700 "$DEST"
echo "Stopping application writers and brokers briefly for a consistent volume archive..."
mapfile -t WAS_RUNNING < <(docker compose ps --services --status running)
docker compose stop backend publisher worker rabbitmq redis postgres
cleanup() {
  for service in "${WAS_RUNNING[@]}"; do
    docker compose start "$service" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT
docker compose run --rm --no-deps -v "$DEST:/backup" --entrypoint sh redis -c 'tar -czf /backup/redis_data.tgz -C /data .'
docker compose run --rm --no-deps -v "$DEST:/backup" --entrypoint sh rabbitmq -c 'tar -czf /backup/rabbitmq_data.tgz -C /var/lib/rabbitmq .'
docker compose run --rm --no-deps -v "$DEST:/backup" --entrypoint sh postgres -c 'tar -czf /backup/postgres_data.tgz -C /var/lib/postgresql/data .'
cp .env "$DEST/env.backup"
chmod 600 "$DEST"/*
echo "Backup written to $DEST; keep a protected copy off-instance and verify restore into an isolated host."
