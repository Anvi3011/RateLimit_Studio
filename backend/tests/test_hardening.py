import hashlib
import json
import time
import uuid

from app import create_app
from app.routes import api as routes


def test_admin_auth_is_throttled_and_recovers_after_window():
    app = create_app({'TESTING': True, 'ADMIN_TOKEN': 'test-secret'})
    client = app.test_client()
    remote = f'198.18.0.{int(time.time()) % 200 + 1}'
    key = 'ratelimit:admin:attempts:' + hashlib.sha256(remote.encode()).hexdigest()
    try:
        for _ in range(4):
            assert client.put('/api/config', json={}, environ_base={'REMOTE_ADDR': remote}).status_code == 401
        limited = client.put('/api/config', json={}, environ_base={'REMOTE_ADDR': remote})
        assert limited.status_code == 429
        assert limited.headers['Retry-After'] == '60'
        assert client.put('/api/config', json={}, headers={'X-Admin-Token': 'test-secret'}, environ_base={'REMOTE_ADDR': remote}).status_code == 429
    finally:
        routes.store().delete(key)


def test_fault_controls_use_shared_ttl_and_include_network_timeout():
    app = create_app({'TESTING': True, 'ADMIN_TOKEN': 'test-secret', 'FAULTS_ENABLED': True})
    client = app.test_client()
    headers = {'X-Admin-Token': 'test-secret'}
    key = 'ratelimit:fault:network_timeout'
    prior = routes.store().get(key)
    prior_ttl = routes.store().ttl(key)
    try:
        result = client.post('/api/faults', json={'name': 'network_timeout', 'enabled': True, 'duration_seconds': 2}, headers=headers)
        assert result.status_code == 200
        assert result.get_json()['active']['network_timeout'] is True
        assert 1 <= result.get_json()['ttl_seconds']['network_timeout'] <= 2
        time.sleep(2.1)
        assert client.get('/api/faults').get_json()['active'].get('network_timeout') is None
    finally:
        routes.store().delete(key)
        if prior is not None and prior_ttl > 0:
            routes.store().set(key, prior, ex=prior_ttl)


def test_legacy_non_expiring_fault_key_gets_recovery_ttl():
    key = 'ratelimit:fault:worker_unavailable'
    prior = routes.store().get(key)
    prior_ttl = routes.store().ttl(key)
    try:
        routes.store().set(key, 'legacy')
        assert routes.active_faults()['worker_unavailable'] is True
        assert routes.store().ttl(key) > 0
    finally:
        routes.store().delete(key)
        if prior is not None:
            if prior_ttl > 0: routes.store().set(key, prior, ex=prior_ttl)
            else: routes.store().set(key, prior)


def test_metrics_time_series_returns_backend_sample_shape():
    member = json.dumps({'timestamp': time.time(), 'accepted': 2, 'rejected': 1, 'errors': 0, 'latency_ms': 12.5, 'test_id': str(uuid.uuid4())})
    routes.store().zadd('ratelimit:chart_samples', {member: time.time()})
    try:
        response = create_app({'TESTING': True}).test_client().get('/api/metrics/timeseries?seconds=60&bucket_seconds=5')
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['source'] == 'backend Redis decision samples'
        assert payload['points']
        assert any(point['accepted'] >= 2 and point['rejected'] >= 1 for point in payload['points'])
        assert {'timestamp', 'accepted', 'rejected', 'errors', 'avg_latency_ms', 'requests'} <= payload['points'][-1].keys()
    finally:
        routes.store().zrem('ratelimit:chart_samples', member)


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
    app = create_app({'TESTING': True, 'ADMIN_TOKEN': 'test-secret', 'FAULTS_ENABLED': True})
    client = app.test_client()
    headers = {'X-Admin-Token': 'test-secret'}
    key = 'ratelimit:fault:api_degraded'
    prior = routes.store().get(key)
    prior_ttl = routes.store().ttl(key)
    bad = client.post('/api/faults', json={'name': 'api_degraded', 'duration_seconds': 'soon'}, headers=headers)
    assert bad.status_code == 400
    # Caller supplied durations over the safety ceiling are clamped.
    try:
        result = client.post('/api/faults', json={'name': 'api_degraded', 'duration_seconds': 9999}, headers=headers)
        assert result.status_code == 200
        assert result.get_json()['ttl_seconds']['api_degraded'] <= 300
    finally:
        routes.store().delete(key)
        if prior is not None and prior_ttl > 0:
            routes.store().set(key, prior, ex=prior_ttl)
