import hashlib
import json
import time
import uuid

from app import create_app
from app.routes import api as routes
from app.session import generate_session_id, validate_session_id

def test_session_id_security_and_forgery_prevention():
    # Valid session has valid signature
    valid_id = generate_session_id()
    assert validate_session_id(valid_id) is True

    # Tampered signature fails validation
    tampered_sig = valid_id[:-4] + 'ffff'
    assert validate_session_id(tampered_sig) is False

    # Arbitrary strings fail validation
    assert validate_session_id('admin') is False
    assert validate_session_id('../../../etc/passwd') is False
    assert validate_session_id('') is False

def test_fault_controls_use_shared_ttl_and_include_network_timeout():
    app = create_app({'TESTING': True, 'FAULTS_ENABLED': True})
    client = app.test_client()
    session_id = generate_session_id()
    client.set_cookie('demo_session_id', session_id)
    key = f'ratelimit:fault:{session_id}:network_timeout'
    try:
        result = client.post('/api/faults', json={'name': 'network_timeout', 'enabled': True, 'duration_seconds': 2})
        assert result.status_code == 200
        assert result.get_json()['active']['network_timeout'] is True
        assert 1 <= result.get_json()['ttl_seconds']['network_timeout'] <= 2
        time.sleep(2.1)
        assert client.get('/api/faults').get_json()['active'].get('network_timeout') is None
    finally:
        routes.store().delete(key)
        routes.store().delete('ratelimit:fault:network_timeout')

def test_legacy_global_fault_key_does_not_affect_session_isolated_active_faults():
    """
    Legacy global fault keys (ratelimit:fault:<name>) must NOT affect session-isolated
    active_faults() checks. The old shared worker-pause behaviour has been removed:
    each visitor's faults are stored under ratelimit:fault:<session_id>:<name>.
    Setting a bare global key must be invisible to active_faults() so that one
    visitor (or a stale key left over from before the migration) cannot accidentally
    affect another visitor's session or the shared RabbitMQ consumer.
    """
    legacy_key = 'ratelimit:fault:worker_unavailable'
    session_key = f'ratelimit:fault:default:worker_unavailable'
    # Ensure neither key pre-exists
    routes.store().delete(legacy_key)
    routes.store().delete(session_key)
    try:
        # Set the old-style bare global key
        routes.store().set(legacy_key, 'legacy')
        # active_faults() with no request context uses sid='default' and checks
        # ratelimit:fault:default:worker_unavailable — NOT the bare legacy key
        faults = routes.active_faults()
        assert 'worker_unavailable' not in faults, (
            "Legacy global fault key must NOT bleed into session-scoped active_faults(). "
            "This would break session isolation."
        )
        # The legacy key should be untouched (no TTL set by active_faults)
        assert routes.store().exists(legacy_key), "Legacy key should still exist — active_faults() must not delete it"
        # Verify that a session-scoped key IS detected correctly
        routes.store().set(session_key, '1', ex=30)
        faults_with_session = routes.active_faults()
        assert faults_with_session.get('worker_unavailable') is True, (
            "Session-scoped fault key ratelimit:fault:default:worker_unavailable must be detected"
        )
        # active_faults() must have stamped a recovery TTL on the non-expiring key
        # (ttl=-1 → expire(key, 60) logic in active_faults)
        # We set it with ex=30 so TTL is already positive — just verify it's bounded
        assert 0 < routes.store().ttl(session_key) <= 60
    finally:
        routes.store().delete(legacy_key)
        routes.store().delete(session_key)

def test_metrics_time_series_returns_backend_sample_shape():
    session_id = generate_session_id()
    member = json.dumps({'timestamp': time.time(), 'accepted': 2, 'rejected': 1, 'errors': 0, 'latency_ms': 12.5, 'test_id': str(uuid.uuid4())})
    routes.store().zadd(f'ratelimit:chart_samples:{session_id}', {member: time.time()})
    client = create_app({'TESTING': True}).test_client()
    client.set_cookie('demo_session_id', session_id)
    try:
        response = client.get('/api/metrics/timeseries?seconds=60&bucket_seconds=5')
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['source'] == 'backend Redis decision samples'
        assert payload['points']
        assert any(point['accepted'] >= 2 and point['rejected'] >= 1 for point in payload['points'])
        assert {'timestamp', 'accepted', 'rejected', 'errors', 'avg_latency_ms', 'requests'} <= payload['points'][-1].keys()
    finally:
        routes.store().delete(f'ratelimit:chart_samples:{session_id}')

def test_outbox_capacity_is_atomic_and_bounded(monkeypatch):
    key = 'ratelimit:test:outbox:' + str(uuid.uuid4())
    monkeypatch.setattr(routes, 'OUTBOX_KEY', key)
    monkeypatch.setattr(routes, 'OUTBOX_CAPACITY', 1)
    try:
        assert routes.enqueue_event({'event_id': 'first'}) is True
        assert routes.enqueue_event({'event_id': 'second'}) is False
        assert routes.store().llen(key) == 1
    finally:
        routes.store().delete(key)

def test_fault_ttl_is_bounded_and_rejects_bad_values():
    app = create_app({'TESTING': True, 'FAULTS_ENABLED': True})
    client = app.test_client()
    session_id = generate_session_id()
    headers = {'X-Demo-Session-ID': session_id}
    key = f'ratelimit:fault:{session_id}:api_degraded'
    bad = client.post('/api/faults', json={'name': 'api_degraded', 'duration_seconds': 'soon'}, headers=headers)
    assert bad.status_code == 400
    try:
        result = client.post('/api/faults', json={'name': 'api_degraded', 'duration_seconds': 9999}, headers=headers)
        assert result.status_code == 200
        assert result.get_json()['ttl_seconds']['api_degraded'] <= 300
    finally:
        routes.store().delete(key)
        routes.store().delete('ratelimit:fault:api_degraded')
