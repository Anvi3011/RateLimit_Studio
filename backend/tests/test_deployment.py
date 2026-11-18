import json
from unittest.mock import Mock
import pytest
from app import create_app
from app.config import validate_public_config
from app.routes import api as routes
from app.db import connection
import db as worker_db


def test_readiness_fails_when_only_database_is_down(monkeypatch):
    r = Mock()
    r.ttl.return_value = 50
    monkeypatch.setattr(routes, 'store', lambda: r)
    import pika
    monkeypatch.setattr(pika, 'BlockingConnection', lambda *_: Mock())
    engine = Mock()
    engine.connect.side_effect = ConnectionError()
    import app.db
    monkeypatch.setattr(app.db, 'get_engine', lambda: engine)
    response = create_app({'TESTING': True}).test_client().get('/api/health')
    assert response.status_code == 503
    assert response.json['database'] == 'unavailable'
    assert response.json['redis'] == 'healthy'
    assert response.json['healthy'] is False


def test_live_does_not_touch_database_session(monkeypatch):
    import app.session
    touch = Mock(side_effect=AssertionError('Health must not touch sessions'))
    monkeypatch.setattr(app.session, 'touch_session', touch)
    client = create_app({'TESTING': True}).test_client()
    response = client.get('/api/live')
    assert response.status_code == 200
    assert 'Set-Cookie' not in response.headers
    touch.assert_not_called()


def test_combined_liveness_detects_child_failure_and_stale_supervisor(monkeypatch, tmp_path):
    import time
    path = tmp_path / 'state.json'
    monkeypatch.setenv('SUPERVISOR_STATUS_FILE', str(path))
    client = create_app({'TESTING': True}).test_client()
    assert client.get('/api/live').status_code == 503
    for running, age, expected in [(True, 0, 200), (False, 0, 503), (True, 20, 503)]:
        path.write_text(json.dumps({'updated_at': time.time() - age,
                                    'running': {'api': True, 'publisher': running, 'worker': True}}))
        assert client.get('/api/live').status_code == expected


@pytest.mark.parametrize('module', [connection, worker_db])
@pytest.mark.parametrize('url', ['', 'sqlite:///:memory:'])
def test_deployed_database_never_falls_back_to_sqlite(monkeypatch, module, url):
    monkeypatch.setenv('REQUIRE_POSTGRES', 'true')
    monkeypatch.setenv('DATABASE_URL', url)
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        module.get_database_url()


def public_env(monkeypatch):
    for key, value in {
        'PUBLIC_DEMO': 'true', 'REQUIRE_POSTGRES': 'true',
        'DATABASE_URL': 'postgresql://user:dummy@db.invalid/db?sslmode=require',
        'REDIS_URL': 'rediss://default:dummy@redis.invalid:6379/0',
        'RABBITMQ_URL': 'amqps://user:dummy@broker.invalid/vhost',
        'SESSION_SECRET': 'test-only-secret-' + 'x' * 40,
        'FRONTEND_ORIGIN': 'https://studio.example', 'FAULTS_ENABLED': 'false',
        'PROXY_X_FOR': '0', 'PROXY_X_PROTO': '0', 'PROXY_X_HOST': '0',
        'SCHEMA_INIT': 'false',
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize('key,value', [
    ('SESSION_SECRET', 'short'), ('REDIS_URL', 'redis://user:dummy@host/0'),
    ('RABBITMQ_URL', 'amqp://user:dummy@host/'), ('FRONTEND_ORIGIN', '*'),
    ('DATABASE_URL', 'postgresql://user:dummy@db/db'), ('FAULTS_ENABLED', 'true'),
])
def test_invalid_public_configuration_fails_closed(monkeypatch, key, value):
    public_env(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        validate_public_config()


def test_public_cookie_csrf_cache_and_cleanup(monkeypatch):
    public_env(monkeypatch)
    validate_public_config()
    # Session touches are stubbed: these tests must never use provider credentials.
    import app.session
    monkeypatch.setattr(app.session, 'touch_session', lambda *_args, **_kwargs: None)
    client = create_app({'TESTING': True}).test_client()
    response = client.get('/api/session', headers={'X-Forwarded-Host': 'attacker.invalid'})
    assert 'Secure;' in response.headers['Set-Cookie']
    assert 'HttpOnly;' in response.headers['Set-Cookie']
    assert 'SameSite=Lax' in response.headers['Set-Cookie']
    assert response.headers['Cache-Control'] == 'no-store'
    sid = response.json['session_id']
    assert client.get('/api/session').json['session_id'] == sid
    assert client.put('/api/config', json={'rate': 7}, headers={
        'Origin': 'https://studio.example', 'Sec-Fetch-Site': 'same-origin'}).status_code == 200
    assert client.put('/api/config', json={'rate': 8}, headers={
        'Origin': 'https://attacker.invalid'}).status_code == 403
    assert client.put('/api/config', json={'rate': 8}, headers={
        'Origin': 'https://studio.example', 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.post('/api/logs/cleanup', headers={'Origin': 'https://studio.example'}).status_code == 403


def test_log_read_failure_is_503_not_empty_success(monkeypatch):
    import app.db.service as service
    factory = Mock()
    factory.return_value.query.side_effect = ConnectionError('private database detail')
    monkeypatch.setattr(service, 'get_session_factory', lambda: factory)
    response = create_app({'TESTING': True}).test_client().get('/api/logs/requests')
    assert response.status_code == 503
    assert 'private database detail' not in response.get_data(as_text=True)
