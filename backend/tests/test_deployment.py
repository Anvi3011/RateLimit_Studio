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

@pytest.mark.parametrize('name,stage,outcome,status', [
    ('redis_unavailable', 'decision', 'unavailable', 503),
    ('rabbitmq_unavailable', 'publish', 'blocked', None),
    ('worker_unavailable', 'persist', 'pending', None),
    ('api_degraded', 'response', 'degraded', 503),
    ('network_timeout', 'response', 'timeout', 504),
])
def test_public_fault_probes_are_isolated_from_real_requests(monkeypatch, name, stage, outcome, status):
    import app.session
    from app.routes import api as routes

    public_env(monkeypatch)
    monkeypatch.setenv('PUBLIC_FAULT_MODE', 'simulation')
    monkeypatch.setattr(app.session, 'touch_session', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, 'log_fault_injection', lambda *_args: None)
    monkeypatch.setattr(routes, 'log_fault_recovery', lambda *_args: None)
    monkeypatch.setattr(routes, 'decide', lambda *_args: {'allowed': True, 'remaining': 1, 'retry_after': 0})
    browser, other = (create_app({'TESTING': True}).test_client() for _ in range(2))
    sid = browser.get('/api/session').json['session_id']
    state = browser.post('/api/faults', json={'name': name, 'duration_seconds': 10}).json
    assert state['mode'] == 'simulation'
    assert state['active'] == {name: True}
    assert 0 <= state['ttl_seconds'][name] <= 10
    key = routes.public_fault_key(sid, name)
    browser.post('/api/faults', json={'name': name, 'duration_seconds': 60})
    assert routes.store().ttl(key) <= 10
    probe = browser.get(f'/api/faults/probe/{name}').json
    assert (probe['stage'], probe['outcome'], probe['simulated_http_status']) == (stage, outcome, status)
    assert other.get(f'/api/faults/probe/{name}').json['outcome'] == 'recovered'
    assert other.delete('/api/faults').json['active'] == {}
    assert browser.get('/api/faults').json['active'] == {name: True}

    # Even keys left over from a local/legacy run cannot change public traffic.
    routes.store().set(f'ratelimit:fault:{sid}:{name}', '1', ex=10)
    response = browser.post('/api/requests/test', json={'client_id': 'real-request'})
    assert response.status_code == 200
    assert 'event_publish_error' not in response.json
    assert routes.store().llen(routes.OUTBOX_KEY) == 1
    assert browser.get('/api/worker/status').json['status'] == other.get('/api/worker/status').json['status']

    routes.store().pexpireat(key, 1)
    assert browser.get('/api/faults').json['active'] == {}
    assert browser.get(f'/api/faults/probe/{name}').json['outcome'] == 'recovered'
    browser.post('/api/faults', json={'name': name})
    assert browser.post('/api/faults', json={'name': name, 'enabled': False}).json['active'] == {}
    browser.post('/api/faults', json={'name': name})
    assert browser.delete('/api/faults').json['active'] == {}


def test_public_fault_security(monkeypatch):
    import app.session
    public_env(monkeypatch)
    monkeypatch.setattr(app.session, 'touch_session', lambda *_args, **_kwargs: None)
    disabled = create_app({'TESTING': True}).test_client()
    assert disabled.post('/api/faults', json={'name': 'api_degraded'}).status_code == 403
    assert disabled.delete('/api/faults').status_code == 403
    assert disabled.get('/api/faults/probe/api_degraded').status_code == 403
    monkeypatch.setenv('PUBLIC_FAULT_MODE', 'legacy')
    with pytest.raises(RuntimeError, match='PUBLIC_FAULT_MODE'):
        validate_public_config()
    monkeypatch.setenv('PUBLIC_FAULT_MODE', 'simulation')
    browser = create_app({'TESTING': True}).test_client()
    for data in ({'name': 'latency_ms', 'value': 2000}, {'name': 'restart_worker'},
                 {'name': 'api_degraded', 'duration_seconds': 61}, {'name': 'api_degraded', 'enabled': 'false'},
                 {'name': 'api_degraded', 'session_id': 'another-user'}, ['api_degraded']):
        assert browser.post('/api/faults', json=data).status_code == 400
    assert browser.post('/api/faults', json={'name': 'api_degraded'},
                        headers={'Origin': 'https://other.example'}).status_code == 403
    assert browser.get('/api/faults').json['active'] == {}
    monkeypatch.setenv('FAULTS_ENABLED', 'true')
    with pytest.raises(RuntimeError, match='FAULTS_ENABLED'):
        validate_public_config()
