"""Fail closed on incomplete public-demo configuration; local defaults are unchanged."""
import os
from urllib.parse import urlsplit, parse_qs


def validate_public_config():
    if os.getenv('PUBLIC_DEMO', 'false') != 'true':
        return
    required_schemes = {'DATABASE_URL': ('postgresql', 'postgres'),
                        'REDIS_URL': ('rediss',), 'RABBITMQ_URL': ('amqps',)}
    for key, schemes in required_schemes.items():
        value = urlsplit(os.getenv(key, ''))
        if value.scheme not in schemes or not value.hostname or not value.password:
            raise RuntimeError(f'{key} must be a complete TLS provider connection URL')
    query = parse_qs(urlsplit(os.environ['DATABASE_URL']).query)
    if query.get('sslmode', [''])[0] not in ('require', 'verify-full', 'verify-ca'):
        raise RuntimeError('DATABASE_URL must require TLS')
    secret = os.getenv('SESSION_SECRET', '')
    if len(secret) < 32 or secret.lower().startswith(('replace', 'change', 'your-')):
        raise RuntimeError('SESSION_SECRET must be a unique random secret of at least 32 characters')
    origins = os.getenv('FRONTEND_ORIGIN', '').split(',')
    if any(not o or urlsplit(o).scheme != 'https' or not urlsplit(o).hostname
           or urlsplit(o).path or '*' in o or 'localhost' in o for o in origins):
        raise RuntimeError('FRONTEND_ORIGIN must list exact HTTPS origins without trailing slashes')
    if os.getenv('REQUIRE_POSTGRES') != 'true':
        raise RuntimeError('Public mode requires REQUIRE_POSTGRES=true')
    if os.getenv('FAULTS_ENABLED', 'false') != 'false':
        raise RuntimeError('Public mode requires FAULTS_ENABLED=false')
