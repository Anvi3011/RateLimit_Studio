#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ -f .env ]] || { echo "Create .env from .env.example and set unique secrets first." >&2; exit 1; }
chmod 600 .env
MODE="${1:-http}"
export DEPLOY_MODE="$MODE"
python3 - <<'PY'
from pathlib import Path
from urllib.parse import urlsplit
import os
values = {}
for raw in Path('.env').read_text().splitlines():
    line = raw.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"\'')
for key in ('RABBITMQ_USER', 'RABBITMQ_PASSWORD', 'POSTGRES_USER', 'POSTGRES_PASSWORD'):
    value = values.get(key, '')
    if not value or value.lower().startswith(('change-me', 'replace-', 'your-')):
        raise SystemExit(f'{key} is missing or remains a placeholder in .env')
if len(values['POSTGRES_PASSWORD']) < 16:
    raise SystemExit('POSTGRES_PASSWORD must contain at least 16 characters')
if len(values['RABBITMQ_PASSWORD']) < 64 or not all(c in '0123456789abcdefABCDEF' for c in values['RABBITMQ_PASSWORD']):
    raise SystemExit('RABBITMQ_PASSWORD must be 64 or more hexadecimal characters (URL-safe for the Compose AMQP URL)')
if values.get('FAULTS_ENABLED', 'false').lower() != 'false':
    raise SystemExit('FAULTS_ENABLED must be false for deployment')
if values.get('HTTP_PORT', '8080') != '80':
    raise SystemExit('Set HTTP_PORT=80 in .env for public HTTP or TLS edge access')
origins = [part.strip() for part in values.get('FRONTEND_ORIGIN', '').split(',')]
if not origins or any(not origin or urlsplit(origin).scheme not in ('http', 'https') or not urlsplit(origin).hostname or urlsplit(origin).hostname in ('localhost', '127.0.0.1', '::1') for origin in origins):
    raise SystemExit('Set FRONTEND_ORIGIN to the public dashboard origin (for example http://EC2_PUBLIC_IP or https://your.domain)')
if os.environ.get('DEPLOY_MODE') == 'tls' and any(urlsplit(origin).scheme != 'https' for origin in origins):
    raise SystemExit('TLS mode requires HTTPS FRONTEND_ORIGIN values')
if os.environ.get('DEPLOY_MODE') == 'tls':
    cert_dir = Path(values.get('TLS_CERT_DIR', './deployment/tls'))
    if not cert_dir.is_absolute(): cert_dir = Path.cwd() / cert_dir
    if not (cert_dir / 'fullchain.pem').is_file() or not (cert_dir / 'privkey.pem').is_file():
        raise SystemExit('TLS mode requires fullchain.pem and privkey.pem in TLS_CERT_DIR')
PY
case "$MODE" in
  http)
    docker compose config --quiet
    docker compose --profile tls stop nginx-tls || true
    docker compose up -d --build
    ;;
  tls)
    [[ "${HEALTHCHECK_URL:-}" == https://* ]] || { echo "Set HEALTHCHECK_URL=https://your-domain/api/health for TLS verification." >&2; exit 1; }
    docker compose config --quiet
    docker compose stop nginx || true
    docker compose --profile tls up -d --build redis rabbitmq backend worker publisher frontend nginx-tls
    ;;
  *) echo "Usage: $0 [http|tls]" >&2; exit 2 ;;
esac
bash deployment/aws/healthcheck.sh
