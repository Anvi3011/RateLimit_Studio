#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
REF="${1:?Usage: rollback.sh <known-good-commit-or-tag> [http|tls]}"
MODE="${2:-http}"
ENV_FILE="$ROOT/.env"
[[ -s "$ENV_FILE" ]] || { echo "Missing .env; rollback stopped." >&2; exit 1; }
git -C "$ROOT" rev-parse --verify "${REF}^{commit}" >/dev/null
COMMIT="$(git -C "$ROOT" rev-parse --short=12 "${REF}^{commit}")"
RELEASE_ROOT="$ROOT/.releases"
mkdir -p "$RELEASE_ROOT"
RELEASE_DIR="$RELEASE_ROOT/rollback-${COMMIT}-$(date -u +%Y%m%dT%H%M%SZ)"
case "$RELEASE_DIR" in "$RELEASE_ROOT"/rollback-*) ;; *) echo "Unsafe release path." >&2; exit 1 ;; esac
mkdir "$RELEASE_DIR"
git -C "$ROOT" archive "$REF" | tar -xf - -C "$RELEASE_DIR"

# Preserve the current Compose project name so Docker reuses its existing named
# volumes. Set COMPOSE_PROJECT_NAME explicitly if the original deploy used -p.
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$ROOT" | tr '[:upper:]' '[:lower:]')}"
DC=(docker compose --project-name "$PROJECT" --file "$RELEASE_DIR/docker-compose.yml" --env-file "$ENV_FILE")
case "$MODE" in
  http)
    "${DC[@]}" config --quiet
    "${DC[@]}" --profile tls stop nginx-tls || true
    "${DC[@]}" up -d --build
    URL="${HEALTHCHECK_URL:-http://localhost:80/api/health}"
    ;;
  tls)
    [[ "${HEALTHCHECK_URL:-}" == https://* ]] || { echo "Set HEALTHCHECK_URL=https://your-domain/api/health for TLS health verification." >&2; exit 1; }
    [[ -s "$ROOT/deployment/tls/fullchain.pem" && -s "$ROOT/deployment/tls/privkey.pem" ]] || { echo "Current trusted TLS certificate/key not found." >&2; exit 1; }
    export TLS_CERT_DIR="$ROOT/deployment/tls"
    DC+=(--profile tls)
    "${DC[@]}" config --quiet
    "${DC[@]}" stop nginx || true
    "${DC[@]}" up -d --build redis rabbitmq backend worker publisher frontend nginx-tls
    URL="$HEALTHCHECK_URL"
    ;;
  *) echo "Usage: rollback.sh <known-good-commit-or-tag> [http|tls]" >&2; exit 2 ;;
esac
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 "$URL"; then
    echo
    echo "Rolled back to $COMMIT using release files at $RELEASE_DIR. Data volumes were retained."
    exit 0
  fi
  sleep 2
done
echo "Rollback started but health did not pass at $URL; inspect services without removing volumes." >&2
"${DC[@]}" ps >&2 || true
"${DC[@]}" logs --tail=100 backend publisher worker nginx nginx-tls >&2 || true
exit 1
