import json, os
from app import create_app

def test_policy_update_is_redis_persistent_and_returns_stored_values():
    app=create_app({'TESTING':True,'ADMIN_TOKEN':os.getenv('ADMIN_TOKEN','test-token')})
    client=app.test_client(); token=os.getenv('ADMIN_TOKEN','test-token')
    old=client.get('/api/config').get_json()
    policy={**old,'algorithm':'token_bucket','rate':2,'burst':4,'window_seconds':3,'edge_nodes':7}
    try:
        response=client.put('/api/config',json=policy,headers={'X-Admin-Token':token})
        assert response.status_code==200
        assert response.get_json()==policy
        assert client.get('/api/config').get_json()==policy
        assert create_app({'TESTING':True}).test_client().get('/api/config').get_json()==policy
    finally:
        client.put('/api/config',json=old,headers={'X-Admin-Token':token})

def test_applied_token_policy_drives_real_api_decisions():
    app=create_app({'TESTING':True,'ADMIN_TOKEN':os.getenv('ADMIN_TOKEN','test-token')})
    client=app.test_client(); token=os.getenv('ADMIN_TOKEN','test-token')
    old=client.get('/api/config').get_json()
    policy={**old,'algorithm':'token_bucket','rate':2,'burst':4,'policy_id':'api-test-'+os.urandom(6).hex()}
    try:
        assert client.put('/api/config',json=policy,headers={'X-Admin-Token':token}).status_code==200
        responses=[client.post('/api/requests/test',json={'client_id':'runtime-check','endpoint':'/check'}) for _ in range(5)]
        assert [r.status_code for r in responses].count(200)==4
        assert [r.status_code for r in responses].count(429)==1
    finally:
        client.put('/api/config',json=old,headers={'X-Admin-Token':token})
