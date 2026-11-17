from unittest.mock import patch
from app import create_app


def test_database_failure_makes_health_unhealthy(monkeypatch):
    from app.routes import api as routes
    from app import db
    class Store:
        def ping(self): return True
        def ttl(self, key): return 60
    monkeypatch.setattr(routes, 'store', lambda: Store())
    app = create_app({'TESTING': True})
    monkeypatch.setattr(db, 'get_engine', lambda: (_ for _ in ()).throw(ConnectionError('database offline')))
    with patch('pika.BlockingConnection'):
        response = app.test_client().get('/api/health')
    assert response.status_code == 503
    assert response.json['database'] == 'unavailable'
    assert response.json['healthy'] is False
