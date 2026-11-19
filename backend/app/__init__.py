import os
import logging
from flask import Flask, jsonify, g, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from .routes.api import api
from .db import init_db
from .session import get_current_session_id
from .config import validate_public_config

logger = logging.getLogger(__name__)


def create_app(test_config=None):
    validate_public_config()
    app = Flask(__name__)

    app.config.update(
        MAX_CONTENT_LENGTH=64 * 1024,
        PUBLIC_DEMO=os.getenv('PUBLIC_DEMO', 'false') == 'true',
        PUBLIC_FAULT_MODE=os.getenv('PUBLIC_FAULT_MODE', 'disabled'),
        FAULTS_ENABLED=os.getenv('FAULTS_ENABLED', 'false').lower() == 'true',
        REDIS_URL=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
        RABBITMQ_URL=os.getenv('RABBITMQ_URL', 'amqp://localhost:5672/%2F'),
        FAIL_MODE=os.getenv('RATE_LIMIT_FAIL_MODE', 'closed'),
        LOG_RETENTION_HOURS=int(os.getenv('LOG_RETENTION_HOURS', '24')),
        SESSION_SECRET=os.getenv('SESSION_SECRET', 'ratelimit-default-dev-secret-change-in-prod')
    )

    if test_config:
        app.config.update(test_config)

    # Initialize database tables
    try:
        if os.getenv('SCHEMA_INIT', 'true') == 'true':
            init_db()
    except Exception as exc:
        logger.warning("Database initialization unavailable (%s)", type(exc).__name__)

    # Only trusted proxy hops may supply forwarding headers.
    # Verify the actual proxy chain before production deployment.
    app.wsgi_app = ProxyFix(app.wsgi_app,
        x_for=int(os.getenv('PROXY_X_FOR', '2')),
        x_proto=int(os.getenv('PROXY_X_PROTO', '1')),
        x_host=int(os.getenv('PROXY_X_HOST', '1')))

    frontend_origins = os.getenv(
        'FRONTEND_ORIGIN',
        'http://localhost,http://localhost:8080'
    ).split(',')

    CORS(
        app,
        origins=frontend_origins,
        supports_credentials=True,
        expose_headers=['X-RateLimit-Remaining', 'Retry-After'],
        allow_headers=['Content-Type', 'X-Client-ID', 'X-Correlation-ID']
    )

    @app.before_request
    def resolve_session():
        # Ensure session is established for all requests.
        if request.path not in ('/api/live', '/api/health'):
            get_current_session_id()

    @app.before_request
    def csrf_protect():
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            if getattr(g, 'internal_traffic', False):
                return None

            # Block cross-site browser requests via Sec-Fetch-Site
            sec_fetch_site = request.headers.get('Sec-Fetch-Site')
            if sec_fetch_site == 'cross-site':
                return jsonify(error='csrf_blocked', detail='Cross-site request blocked'), 403

            # Check Origin header if present
            origin = request.headers.get('Origin')
            if origin:
                normalized_origin = origin.rstrip('/')
                allowed = set(o.rstrip('/') for o in frontend_origins if o)
                if os.getenv('PUBLIC_DEMO', 'false') != 'true':
                    allowed.add(request.host_url.rstrip('/'))
                if any('localhost' in o or '127.0.0.1' in o for o in allowed):
                    allowed.update([
                        'http://localhost',
                        'http://localhost:8080',
                        'http://localhost:5000',
                        'http://127.0.0.1',
                        'http://127.0.0.1:8080',
                        'http://127.0.0.1:5000'
                    ])
                if normalized_origin not in allowed:
                    return jsonify(error='csrf_blocked', detail='Untrusted origin'), 403

    @app.after_request
    def set_session_cookie(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if hasattr(g, 'session_id') and g.session_id:
            response.set_cookie(
                'demo_session_id',
                g.session_id,
                httponly=True,
                secure=request.is_secure or os.getenv('PUBLIC_DEMO', 'false') == 'true',
                samesite='Lax',
                path='/',
                max_age=int(app.config.get('LOG_RETENTION_HOURS', 24)) * 3600
            )
        return response

    app.register_blueprint(api, url_prefix='/api')

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='request_too_large'), 413

    @app.errorhandler(404)
    def missing(_):
        return jsonify(error='not_found'), 404

    return app
