import os
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from .routes.api import api

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024, ADMIN_TOKEN=os.getenv('ADMIN_TOKEN', 'change-me-before-use'),
                      FAULTS_ENABLED=os.getenv('FAULTS_ENABLED', 'false').lower() == 'true',
                      REDIS_URL=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
                      RABBITMQ_URL=os.getenv('RABBITMQ_URL', 'amqp://localhost:5672/%2F'),
                      FAIL_MODE=os.getenv('RATE_LIMIT_FAIL_MODE', 'closed'))
    if test_config: app.config.update(test_config)
    # Only the two private Nginx hops may supply trusted forwarding headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1)
    CORS(app, origins=os.getenv('FRONTEND_ORIGIN', 'http://localhost').split(','))
    app.register_blueprint(api, url_prefix='/api')
    @app.errorhandler(413)
    def too_large(_): return jsonify(error='request_too_large'), 413
    @app.errorhandler(404)
    def missing(_): return jsonify(error='not_found'), 404
    return app
