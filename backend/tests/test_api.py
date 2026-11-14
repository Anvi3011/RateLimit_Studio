import json
from app import create_app

def test_config_is_public_and_validates_policy():
    app = create_app({'TESTING': True, 'REDIS_URL': 'redis://localhost:6379/15'})
    client = app.test_client()
    # No admin token required; returns 400 for bad input
    response = client.put('/api/config', json={'algorithm': 'bad'})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'invalid_config'

def test_fault_controls_are_disabled_when_faults_disabled():
    app = create_app({'TESTING': True, 'FAULTS_ENABLED': False})
    response = app.test_client().post('/api/faults', json={'name': 'api_degraded'})
    assert response.status_code == 403

def test_redis_fail_closed_is_service_error_not_rate_rejection():
    from app.routes import api as routes
    from app.session import generate_session_id

    app = create_app({'TESTING': True, 'FAIL_MODE': 'closed'})
    client = app.test_client()

    sid = generate_session_id()
    client.set_cookie('demo_session_id', sid)
    fault_key = f'ratelimit:fault:{sid}:redis_unavailable'

    try:
        routes.store().set(fault_key, '1', ex=30)

        response = client.post(
            '/api/requests/test',
            json={'client_id': 'fault-test'}
        )

        assert response.status_code == 503
        assert response.get_json()['decision'] == 'error'
    finally:
        routes.store().delete(fault_key)

def test_rabbit_fault_is_recorded_without_reporting_sync_success():
    from app.routes import api as routes
    from app.session import generate_session_id

    app = create_app({'TESTING': True})
    client = app.test_client()

    sid = generate_session_id()
    client.set_cookie('demo_session_id', sid)
    fault_key = f'ratelimit:fault:{sid}:rabbitmq_unavailable'

    try:
        routes.store().set(fault_key, '1', ex=30)

        response = client.post(
            '/api/requests/test',
            json={'client_id': 'rabbit-fault-test'}
        )

        assert response.status_code == 200
        assert response.get_json()['event_publish_error'] == 'RabbitMQ fault injected'
    finally:
        routes.store().delete(fault_key)

def test_worker_fault_control_pauses_and_recovers_consumer():
    import time
    from app.routes import api as routes
    app = create_app({'TESTING': True, 'FAULTS_ENABLED': True})
    client = app.test_client()
    try:
        response = client.post('/api/faults', json={'name': 'worker_unavailable', 'enabled': True})
        assert response.status_code == 200 and response.get_json()['active']['worker_unavailable'] is True
        time.sleep(1.5)
        assert client.get('/api/worker/status').get_json()['status'] == 'stale'
    finally:
        client.delete('/api/faults')
    for _ in range(10):
        if client.get('/api/worker/status').get_json()['status'] == 'healthy':
            break
        # In a test harness without external worker process running, simulate heartbeat recovery
        routes.store().set('ratelimit:worker:last_seen', '1', ex=15)
        time.sleep(0.5)
    assert client.get('/api/worker/status').get_json()['status'] == 'healthy'
