import unittest
from unittest.mock import MagicMock, patch
from app import create_app

class TestAdminAuthentication(unittest.TestCase):
    def setUp(self):
        self.secret = 'test-admin-secret-token-12345678'
        self.app = create_app({'TESTING': True, 'ADMIN_TOKEN': self.secret})
        self.client = self.app.test_client()

    @patch('app.routes.api.store')
    def test_correct_admin_token_via_x_admin_token(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = '0'
        mock_store.return_value = mock_redis

        response = self.client.get('/api/auth/verify', headers={'X-Admin-Token': self.secret})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('authenticated'))

    @patch('app.routes.api.store')
    def test_correct_admin_token_via_bearer_authorization(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = '0'
        mock_store.return_value = mock_redis

        response = self.client.get('/api/auth/verify', headers={'Authorization': f'Bearer {self.secret}'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('authenticated'))

    @patch('app.routes.api.store')
    def test_token_with_whitespace_and_quotes(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = '0'
        mock_store.return_value = mock_redis

        # User entered token with leading/trailing spaces
        response = self.client.get('/api/auth/verify', headers={'X-Admin-Token': f'  "{self.secret}"  '})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('authenticated'))

    @patch('app.routes.api.store')
    def test_incorrect_token_returns_admin_auth_invalid(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = '0'
        mock_store.return_value = mock_redis

        response = self.client.get('/api/auth/verify', headers={'X-Admin-Token': 'wrong-token'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json().get('error'), 'admin_auth_invalid')

    @patch('app.routes.api.store')
    def test_empty_token_returns_admin_auth_required(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = '0'
        mock_store.return_value = mock_redis

        response = self.client.get('/api/auth/verify')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json().get('error'), 'admin_auth_required')

    def test_missing_server_secret_returns_admin_secret_not_configured(self):
        unconfigured_app = create_app({'TESTING': True, 'ADMIN_TOKEN': 'change-me-before-use'})
        client = unconfigured_app.test_client()

        response = client.get('/api/auth/verify', headers={'X-Admin-Token': 'any-token'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json().get('error'), 'admin_secret_not_configured')

    @patch('app.routes.api.store')
    def test_put_config_with_valid_token_saves_and_strips_token_from_body(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_store.return_value = mock_redis

        policy = {
            'algorithm': 'token_bucket',
            'rate': 10,
            'burst': 20,
            'window_seconds': 2,
            'edge_nodes': 4,
            'identity_strategy': 'header',
            'endpoint': '/api/test',
            'admin_token': self.secret
        }
        response = self.client.put('/api/config', json=policy, headers={'X-Admin-Token': self.secret})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get('rate'), 10)
        self.assertNotIn('admin_token', data)

    @patch('app.routes.api.store')
    def test_put_config_sliding_window(self, mock_store):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_store.return_value = mock_redis

        policy = {
            'algorithm': 'sliding_window',
            'rate': 15,
            'burst': 30,
            'window_seconds': 5,
            'edge_nodes': 2,
            'identity_strategy': 'ip',
            'endpoint': '/api/sliding'
        }
        response = self.client.put('/api/config', json=policy, headers={'Authorization': f'Bearer {self.secret}'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get('algorithm'), 'sliding_window')

    @patch.dict('os.environ', {'ADMIN_SECRET': 'alternative-secret-var-987654321'})
    def test_alternative_env_admin_secret(self):
        alt_app = create_app({'TESTING': True})
        self.assertEqual(alt_app.config['ADMIN_TOKEN'], 'alternative-secret-var-987654321')

    @patch.dict('os.environ', {'ADMIN_PASSWORD': 'password-secret-var-987654321'})
    def test_alternative_env_admin_password(self):
        alt_app = create_app({'TESTING': True})
        self.assertEqual(alt_app.config['ADMIN_TOKEN'], 'password-secret-var-987654321')

if __name__ == '__main__':
    unittest.main()
