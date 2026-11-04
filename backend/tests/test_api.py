import json
from app import create_app

def test_config_requires_admin_and_validates_policy():
    app=create_app({'TESTING':True,'ADMIN_TOKEN':'secret','REDIS_URL':'redis://localhost:6379/15'})
    client=app.test_client()
    assert client.put('/api/config',json={'algorithm':'bad'}).status_code==401
    response=client.put('/api/config',json={'algorithm':'bad'},headers={'X-Admin-Token':'secret'})
    assert response.status_code==400

def test_fault_controls_are_disabled_by_default():
    app=create_app({'TESTING':True,'ADMIN_TOKEN':'secret','FAULTS_ENABLED':False})
    response=app.test_client().post('/api/faults',json={'name':'api_degraded'},headers={'X-Admin-Token':'secret'})
    assert response.status_code==403

def test_redis_fail_closed_is_service_error_not_rate_rejection():
    from app.routes import api as routes
    app=create_app({'TESTING':True,'ADMIN_TOKEN':'secret','FAIL_MODE':'closed'})
    previous=routes.FAULTS.copy()
    try:
        routes.FAULTS['redis_unavailable']=True
        response=app.test_client().post('/api/requests/test',json={'client_id':'fault-test'})
        assert response.status_code==503
        assert response.get_json()['decision']=='error'
    finally:
        routes.FAULTS.clear(); routes.FAULTS.update(previous)

def test_rabbit_fault_is_recorded_without_reporting_sync_success():
    from app.routes import api as routes
    app=create_app({'TESTING':True,'ADMIN_TOKEN':'secret'})
    previous=routes.FAULTS.copy()
    try:
        routes.FAULTS['rabbitmq_unavailable']=True
        response=app.test_client().post('/api/requests/test',json={'client_id':'rabbit-fault-test'})
        assert response.status_code==200
        assert response.get_json()['event_publish_error']=='RabbitMQ fault injected'
    finally:
        routes.FAULTS.clear(); routes.FAULTS.update(previous)

def test_worker_fault_control_pauses_and_recovers_consumer():
    import time
    app=create_app({'TESTING':True,'ADMIN_TOKEN':'secret','FAULTS_ENABLED':True})
    client=app.test_client(); headers={'X-Admin-Token':'secret'}
    try:
        response=client.post('/api/faults',json={'name':'worker_unavailable','enabled':True},headers=headers)
        assert response.status_code==200 and response.get_json()['active']['worker_unavailable'] is True
        time.sleep(1.5)
        assert client.get('/api/worker/status').get_json()['status']=='stale'
    finally:
        client.delete('/api/faults',headers=headers)
    for _ in range(10):
        if client.get('/api/worker/status').get_json()['status']=='healthy': break
        time.sleep(.5)
    assert client.get('/api/worker/status').get_json()['status']=='healthy'
