import io
import csv
import json
import unittest
import uuid
from unittest.mock import patch, MagicMock
from app import create_app
from app.session import generate_session_id, validate_session_id
from app.db import init_db, get_engine, get_request_logs, RequestLog, ConfigurationLog, FaultInjectionLog, get_db_session

import fakeredis

class TestPublicAccessAndSessionIsolation(unittest.TestCase):
    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis(decode_responses=True)
        self.patcher = patch('app.routes.api.store', return_value=self.fake_redis)
        self.mock_store = self.patcher.start()
        self.addCleanup(self.patcher.stop)

        self.app = create_app({'TESTING': True, 'FAULTS_ENABLED': True})
        self.client = self.app.test_client()
        init_db(get_engine('sqlite:///:memory:'))

    def test_public_access_no_admin_token_needed(self):
        """Verify all demo endpoints are completely accessible without admin tokens."""
        # Session endpoint
        res = self.client.get('/api/session')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('session_id').startswith('s_'))
        self.assertTrue(validate_session_id(data['session_id']))

        # Config endpoint
        res = self.client.get('/api/config')
        self.assertEqual(res.status_code, 200)

        # Config update without any tokens or headers
        policy = {
            'algorithm': 'token_bucket',
            'rate': 7,
            'burst': 14,
            'window_seconds': 2,
            'edge_nodes': 3,
            'identity_strategy': 'header',
            'endpoint': '/api/public-test'
        }
        res = self.client.put('/api/config', json=policy)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['rate'], 7)

    def test_forged_session_id_is_rejected_and_replaced(self):
        """Verify that visitors cannot submit arbitrary/forged session IDs."""
        forged_id = 's_forged12345678901234567890123456_fake0123456789ab'
        client = self.app.test_client()
        client.set_cookie('demo_session_id', forged_id)
        res = client.get('/api/session')
        self.assertEqual(res.status_code, 200)
        # Server should have detected invalid signature and replaced it with a valid one
        returned_id = res.get_json()['session_id']
        self.assertNotEqual(returned_id, forged_id)
        self.assertTrue(validate_session_id(returned_id))

    def test_session_isolation_configuration(self):
        """Verify two visitors have completely isolated configurations."""
        session_a = generate_session_id()
        session_b = generate_session_id()

        client_a = self.app.test_client()
        client_a.set_cookie('demo_session_id', session_a)

        client_b = self.app.test_client()
        client_b.set_cookie('demo_session_id', session_b)

        # Visitor A sets Token Bucket rate=10
        policy_a = {'algorithm': 'token_bucket', 'rate': 10, 'burst': 20, 'window_seconds': 1, 'edge_nodes': 2, 'identity_strategy': 'header', 'endpoint': '/api/a'}
        res_a = client_a.put('/api/config', json=policy_a)
        self.assertEqual(res_a.status_code, 200)

        # Visitor B sets Sliding Window rate=35
        policy_b = {'algorithm': 'sliding_window', 'rate': 35, 'burst': 70, 'window_seconds': 5, 'edge_nodes': 4, 'identity_strategy': 'ip', 'endpoint': '/api/b'}
        res_b = client_b.put('/api/config', json=policy_b)
        self.assertEqual(res_b.status_code, 200)

        # Read back Visitor A config
        get_a = client_a.get('/api/config')
        self.assertEqual(get_a.get_json()['rate'], 10)
        self.assertEqual(get_a.get_json()['algorithm'], 'token_bucket')

        # Read back Visitor B config
        get_b = client_b.get('/api/config')
        self.assertEqual(get_b.get_json()['rate'], 35)
        self.assertEqual(get_b.get_json()['algorithm'], 'sliding_window')

    def test_session_isolation_logs(self):
        """Verify Visitor A cannot read Visitor B's logs."""
        session_a = generate_session_id()
        session_b = generate_session_id()

        client_a = self.app.test_client()
        client_a.set_cookie('demo_session_id', session_a)

        client_b = self.app.test_client()
        client_b.set_cookie('demo_session_id', session_b)

        # Visitor A writes config
        client_a.put('/api/config', json={'rate': 8})
        # Visitor B writes config
        client_b.put('/api/config', json={'rate': 99})

        # Check configuration logs for session A
        res_a = client_a.get('/api/logs/configurations')
        self.assertEqual(res_a.status_code, 200)
        items_a = res_a.get_json()['items']
        self.assertTrue(len(items_a) > 0)
        for item in items_a:
            self.assertEqual(item['demo_session_id'], session_a)

        # Check configuration logs for session B
        res_b = client_b.get('/api/logs/configurations')
        self.assertEqual(res_b.status_code, 200)
        items_b = res_b.get_json()['items']
        self.assertTrue(len(items_b) > 0)
        for item in items_b:
            self.assertEqual(item['demo_session_id'], session_b)

    def test_fault_injection_and_recovery_logging(self):
        """Verify fault injection records to database and logs recovery."""
        session_id = generate_session_id()
        client = self.app.test_client()
        client.set_cookie('demo_session_id', session_id)

        # Inject simulated Redis fault
        res = client.post('/api/faults', json={'name': 'redis_unavailable', 'duration_seconds': 30})
        self.assertEqual(res.status_code, 200)

        # Verify fault log entry
        fault_logs = client.get('/api/logs/faults')
        self.assertEqual(fault_logs.status_code, 200)
        items = fault_logs.get_json()['items']
        self.assertTrue(any(f['fault_type'] == 'redis_unavailable' and f['fault_status'] == 'ACTIVE' for f in items))

        # Clear fault
        res_del = client.delete('/api/faults')
        self.assertEqual(res_del.status_code, 200)

        # Verify recovery was recorded
        fault_logs_after = client.get('/api/logs/faults')
        items_after = fault_logs_after.get_json()['items']
        self.assertTrue(any(f['fault_type'] == 'redis_unavailable' and f['fault_status'] == 'CLEARED' for f in items_after))

    def test_csv_export_formula_injection_protection(self):
        """Verify safe CSV export neutralizes formula injection attacks."""
        session_id = generate_session_id()
        client = self.app.test_client()
        client.set_cookie('demo_session_id', session_id)

        # Insert a simulated request log with potential formula injection
        session = get_db_session()
        malicious_log = RequestLog(
            request_id=f'req-csv-formula-{uuid.uuid4().hex}',
            correlation_id='corr-1',
            demo_session_id=session_id,
            client_id='=cmd|"/C calc"!A0',  # Formula injection attempt
            algorithm='token_bucket',
            endpoint='+malicious_endpoint',
            http_method='POST',
            request_timestamp=None,
            response_status=200,
            rate_limit_decision='ALLOWED',
            rejection_reason='-100',
            request_processing_time_ms=1.23,
            configured_rate_limit=5,
            burst_capacity=10,
            window_duration=1,
            edge_node_id='edge-0'
        )
        session.add(malicious_log)
        session.commit()

        # Export CSV
        res = client.get('/api/logs/requests/export.csv')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'text/csv')

        csv_text = res.data.decode('utf-8')
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Injected characters must be neutralized with leading single quote
        self.assertTrue(row['client_id'].startswith("'="))
        self.assertTrue(row['endpoint'].startswith("'+"))
        self.assertTrue(row['rejection_reason'].startswith("'-"))

    def test_log_filtering_and_pagination(self):
        """Verify filtering by decision, algorithm, client_id, and pagination."""
        session_id = generate_session_id()
        session = get_db_session()

        for i in range(15):
            log = RequestLog(
                request_id=f'req-filter-{uuid.uuid4().hex}-{i}',
                demo_session_id=session_id,
                client_id=f'client-{"A" if i % 2 == 0 else "B"}',
                algorithm='token_bucket' if i < 10 else 'sliding_window',
                endpoint='/api/demo',
                http_method='POST',
                response_status=200 if i % 3 != 0 else 429,
                rate_limit_decision='ALLOWED' if i % 3 != 0 else 'REJECTED',
                request_processing_time_ms=2.5
            )
            session.add(log)
        session.commit()

        client = self.app.test_client()
        client.set_cookie('demo_session_id', session_id)

        # Filter by decision=ALLOWED
        res_allowed = client.get('/api/logs/requests?decision=ALLOWED')
        self.assertEqual(res_allowed.status_code, 200)
        items = res_allowed.get_json()['items']
        self.assertTrue(all(item['rate_limit_decision'] == 'ALLOWED' for item in items))

        # Filter by algorithm=sliding_window
        res_alg = client.get('/api/logs/requests?algorithm=sliding_window')
        self.assertEqual(res_alg.status_code, 200)
        self.assertEqual(res_alg.get_json()['total'], 5)

        # Pagination: page_size=5
        res_page = client.get('/api/logs/requests?page=2&page_size=5')
        self.assertEqual(res_page.status_code, 200)
        data = res_page.get_json()
        self.assertEqual(len(data['items']), 5)
        self.assertEqual(data['page'], 2)
        self.assertEqual(data['total'], 15)

    def test_system_events_filtering(self):
        """Verify system events can be queried and filtered by severity."""
        session_id = generate_session_id()
        client = self.app.test_client()
        client.set_cookie('demo_session_id', session_id)

        # Trigger traffic to log system event
        client.post('/api/traffic/generate', json={'count': 2, 'requests_per_second': 100})

        res = client.get('/api/logs/system-events?severity=INFO')
        self.assertEqual(res.status_code, 200)
        events = res.get_json()['items']
        self.assertTrue(any(e['event_type'] == 'TRAFFIC_GENERATION_STARTED' for e in events))

    def test_spoofed_session_header_is_ignored(self):
        """Verify X-Demo-Session-ID header cannot hijack or override session."""
        legit_session = generate_session_id()
        victim_session = generate_session_id()
        client = self.app.test_client()
        client.set_cookie('demo_session_id', legit_session)
        res = client.get('/api/session', headers={'X-Demo-Session-ID': victim_session})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['session_id'], legit_session)

    def test_cross_site_request_forgery_is_blocked(self):
        """Verify cross-site mutating requests are rejected by CSRF protection."""
        session_id = generate_session_id()
        client = self.app.test_client()
        client.set_cookie('demo_session_id', session_id)

        # Test Sec-Fetch-Site: cross-site
        res_sec = client.post('/api/config', json={'rate': 20}, headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(res_sec.status_code, 403)
        self.assertEqual(res_sec.get_json()['error'], 'csrf_blocked')

        # Test untrusted Origin
        res_origin = client.post('/api/config', json={'rate': 20}, headers={'Origin': 'http://malicious-site.com'})
        self.assertEqual(res_origin.status_code, 403)
        self.assertEqual(res_origin.get_json()['error'], 'csrf_blocked')

    def test_session_metrics_and_history_isolation(self):
        """Verify Visitor B cannot see Visitor A's metrics, history, or benchmarks."""
        session_a = generate_session_id()
        session_b = generate_session_id()

        client_a = self.app.test_client()
        client_a.set_cookie('demo_session_id', session_a)

        client_b = self.app.test_client()
        client_b.set_cookie('demo_session_id', session_b)

        # Visitor A makes a request
        res_a = client_a.post('/api/requests/test', json={'client_id': 'visitor-a-client'})
        self.assertEqual(res_a.status_code, 200)

        # Visitor A sees 1 request in history and metrics
        m_a = client_a.get('/api/metrics').get_json()
        self.assertEqual(m_a['total'], 1)
        h_a = client_a.get('/api/history').get_json()
        self.assertEqual(h_a['total'], 1)

        # Visitor B has made 0 requests, must see 0 (NO global fallback)
        m_b = client_b.get('/api/metrics').get_json()
        self.assertEqual(m_b['total'], 0)
        h_b = client_b.get('/api/history').get_json()
        self.assertEqual(h_b['total'], 0)

if __name__ == '__main__':
    unittest.main()
