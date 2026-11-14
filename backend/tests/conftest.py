import os
import pytest
import fakeredis
import redis

@pytest.fixture(autouse=True)
def redis_fallback_if_needed(monkeypatch):
    """Ensure tests run cleanly even if local standalone Redis server is not running."""
    try:
        r = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'), socket_connect_timeout=0.2)
        r.ping()
    except Exception:
        fake = fakeredis.FakeRedis(decode_responses=True)
        from app.routes import api as routes_api
        from app import redis as app_redis
        monkeypatch.setattr(routes_api, 'store', lambda: fake)
        monkeypatch.setattr(app_redis, 'client', lambda: fake)
