import os
import uuid
import hmac
import hashlib
import re
from flask import request, g, has_request_context, current_app
from .db import touch_session

SESSION_PATTERN = re.compile(r'^s_[a-f0-9]{32}_[a-f0-9]{16}$')
RETENTION_HOURS = int(os.getenv('LOG_RETENTION_HOURS', '24'))

def _get_secret():
    if has_request_context():
        try:
            secret = current_app.config.get('SESSION_SECRET')
            if secret:
                return secret
        except Exception:
            pass
    return os.getenv('SESSION_SECRET', 'ratelimit-default-dev-secret-change-in-prod')

def generate_session_id():
    secret = _get_secret()
    raw = uuid.uuid4().hex
    sig = hmac.new(secret.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()[:16]
    return f"s_{raw}_{sig}"

def validate_session_id(session_id):
    if not session_id or not isinstance(session_id, str):
        return False
    if not SESSION_PATTERN.match(session_id):
        return False
    parts = session_id.split('_')
    raw, sig = parts[1], parts[2]
    secret = _get_secret()
    expected_sig = hmac.new(secret.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(sig, expected_sig)

def get_current_session_id():
    """Returns the validated demo session ID for the current request context."""
    if not has_request_context():
        return 'default'

    if hasattr(g, 'session_id') and g.session_id:
        return g.session_id

    # Check cookie
    candidate = request.cookies.get('demo_session_id')
    if candidate and validate_session_id(candidate.strip()):
        g.session_id = candidate.strip()
        g.is_new_session = False
        _record_touch(g.session_id)
        return g.session_id

    # Generate new session
    new_id = generate_session_id()
    g.session_id = new_id
    g.is_new_session = True
    _record_touch(new_id)
    return g.session_id

def _record_touch(session_id):
    try:
        touch_session(session_id, retention_hours=RETENTION_HOURS)
    except Exception:
        pass
