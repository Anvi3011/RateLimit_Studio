import json, os
from app import create_app
from app.session import generate_session_id

def test_policy_update_is_redis_persistent_and_returns_stored_values():
    app = create_app({'TESTING': True})
    client = app.test_client()
    session_id = generate_session_id()
    client.set_cookie('demo_session_id', session_id)

    old = client.get('/api/config').get_json()
    policy = {**old, 'algorithm': 'token_bucket', 'rate': 2, 'burst': 4, 'window_seconds': 3, 'edge_nodes': 7}
    try:
        response = client.put('/api/config', json=policy)
        assert response.status_code == 200
        assert response.get_json()['rate'] == policy['rate']
        assert client.get('/api/config').get_json()['rate'] == policy['rate']
    finally:
        client.put('/api/config', json=old)

def test_applied_token_policy_drives_real_api_decisions():
    app = create_app({'TESTING': True})
    client = app.test_client()
    session_id = generate_session_id()
    client.set_cookie('demo_session_id', session_id)

    old = client.get('/api/config').get_json()
    policy = {**old, 'algorithm': 'token_bucket', 'rate': 2, 'burst': 4, 'policy_id': 'api-test-' + os.urandom(6).hex()}
    try:
        assert client.put('/api/config', json=policy).status_code == 200
        responses = [client.post('/api/requests/test', json={'client_id': 'runtime-check', 'endpoint': '/check'}) for _ in range(5)]
        assert [r.status_code for r in responses].count(200) == 4
        assert [r.status_code for r in responses].count(429) == 1
    finally:
        client.put('/api/config', json=old)

def test_algorithm_switch_with_same_client_uses_saved_endpoint_and_isolates_session():
    app = create_app({'TESTING': True})
    browser, other = app.test_client(), app.test_client()
    settings = {'rate': 1, 'burst': 1, 'window_seconds': 1, 'endpoint': '/saved'}
    body = {'client_id': 'algorithm-switch', 'algorithm': 'untrusted-draft', 'rate': 100}
    for algorithm in ('token_bucket', 'sliding_window'):
        assert browser.put('/api/config', json={**settings, 'algorithm': algorithm}).status_code == 200
        assert browser.get('/api/config').json['algorithm'] == algorithm
        allowed = browser.post('/api/requests/test', json=body)
        rejected = browser.post('/api/requests/test', json=body)
        assert (allowed.status_code, rejected.status_code) == (200, 429)
        assert rejected.json['algorithm'] == algorithm
        assert rejected.json['endpoint'] == '/saved'
        assert rejected.json['decision'] == 'rejected'
    assert other.get('/api/config').json['rate'] == 5
    assert other.get('/api/history').json['total'] == 0


def test_policy_read_failure_is_visible_and_fails_closed(monkeypatch):
    from app.routes import api as routes

    app = create_app({'TESTING': True, 'FAIL_MODE': 'closed'})

    def unavailable(*_):
        raise ConnectionError('isolated Redis unavailable')

    monkeypatch.setattr(routes, 'config_value', unavailable)
    browser = app.test_client()
    assert browser.get('/api/config').status_code == 503
    response = browser.post('/api/requests/test', json={'client_id': 'saved-policy-failure'})
    assert response.status_code == 503
    assert response.json['decision'] == 'error'


def test_invalid_config_returns_actionable_error():
    browser = create_app({'TESTING': True}).test_client()
    for body in ({'rate': 1.5}, {'rate': True}, []):
        response = browser.put('/api/config', json=body)
        assert response.status_code == 400
        assert response.json['detail'] in ('rate must be an integer', 'JSON object required')
    assert browser.get('/api/config').json['rate'] == 5
