"""Fault lifecycle tests against real SQL transactions and a deterministic Redis clock."""
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import create_app
from app.db.connection import Base
from app.db.models import FaultInjectionLog, SystemEvent
from app.db import service
from app.faults import start_fault_reconciler
from app.session import generate_session_id


class ClockRedis:
    def __init__(self):
        self.now = 1_800_000_000.0
        self.keys = {}
        self.unavailable = False

    def time(self):
        if self.unavailable:
            raise ConnectionError('Redis unavailable')
        seconds = int(self.now)
        return seconds, round((self.now - seconds) * 1_000_000)

    def set(self, key, value, pxat):
        self.keys[key] = (value, pxat / 1000)

    def exists(self, key):
        self.time()
        return key in self.keys and self.keys[key][1] > self.now

    def delete(self, key):
        self.time()
        self.keys.pop(key, None)


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "faults.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, 'get_session_factory', lambda: factory)
    redis = ClockRedis()
    from app.routes import api
    monkeypatch.setattr(api, 'store', lambda: redis)
    yield factory, redis
    engine.dispose()


def inject(redis, sid='session-a', duration=10, name='worker_unavailable'):
    return service.log_fault_injection(sid, name, 'Worker Consumer', duration, redis_client=redis)


def snapshot(factory, record_id):
    with factory() as session:
        return session.get(FaultInjectionLog, record_id).to_dict()


def recoveries(factory):
    with factory() as session:
        return session.query(SystemEvent).filter_by(event_type='FAULT_INJECTION_RECOVERED').count()


def test_injection_records_matching_redis_deadline(lifecycle):
    factory, redis = lifecycle
    entry = inject(redis)
    assert entry['fault_status'] == 'ACTIVE'
    assert entry['recovery_status'] == 'PENDING'
    assert entry['recovery_timestamp'] is None
    deadline = datetime.fromisoformat(entry['simulation_end_time']).replace(tzinfo=timezone.utc).timestamp()
    assert redis.keys['ratelimit:fault:session-a:worker_unavailable'][1] == deadline
    assert snapshot(factory, entry['id'])['fault_status'] == 'ACTIVE'


def test_expiry_is_not_early_and_is_idempotent(lifecycle):
    factory, redis = lifecycle
    entry = inject(redis)
    redis.now += 9.999
    assert service.reconcile_expired_faults(redis) == 0
    assert snapshot(factory, entry['id'])['fault_status'] == 'ACTIVE'
    redis.now += .002
    assert service.reconcile_expired_faults(redis) == 1
    first = snapshot(factory, entry['id'])
    assert (first['fault_status'], first['recovery_status']) == ('EXPIRED', 'AUTO_RECOVERED')
    assert first['recovery_timestamp'] == first['simulation_end_time']
    assert service.reconcile_expired_faults(redis) == 0
    assert service.log_fault_recovery('session-a', 'worker_unavailable', redis_client=redis) == 0
    assert snapshot(factory, entry['id']) == first
    assert recoveries(factory) == 1


def test_manual_restore_is_scoped_and_repeated_restore_is_noop(lifecycle):
    factory, redis = lifecycle
    a = inject(redis)
    b = inject(redis, sid='session-b')
    latency = inject(redis, name='latency_ms')
    redis.now += 2
    assert service.log_fault_recovery('session-a', 'worker_unavailable', redis_client=redis) == 1
    first = snapshot(factory, a['id'])
    assert (first['fault_status'], first['recovery_status']) == ('CLEARED', 'MANUAL_RECOVERED')
    assert datetime.fromisoformat(first['recovery_timestamp']).replace(tzinfo=timezone.utc).timestamp() == redis.now
    assert not redis.exists('ratelimit:fault:session-a:worker_unavailable')
    assert redis.exists('ratelimit:fault:session-b:worker_unavailable')
    assert snapshot(factory, b['id'])['fault_status'] == 'ACTIVE'
    assert snapshot(factory, latency['id'])['fault_status'] == 'ACTIVE'
    redis.now += 1
    assert service.log_fault_recovery('session-a', 'worker_unavailable', redis_client=redis) == 0
    assert snapshot(factory, a['id']) == first
    assert recoveries(factory) == 1


def test_restore_after_expiry_preserves_automatic_cause(lifecycle):
    factory, redis = lifecycle
    entry = inject(redis)
    redis.now += 11
    service.log_fault_recovery('session-a', 'worker_unavailable', redis_client=redis)
    row = snapshot(factory, entry['id'])
    assert row['fault_status'] == 'EXPIRED'
    assert row['recovery_status'] == 'AUTO_RECOVERED'
    assert row['recovery_timestamp'] == row['simulation_end_time']


def test_reinjection_cannot_expire_or_restore_wrong_generation(lifecycle):
    factory, redis = lifecycle
    old = inject(redis)
    redis.now += 2
    new = inject(redis, duration=60)
    assert snapshot(factory, old['id'])['recovery_status'] == 'SUPERSEDED'
    redis.now += 9
    assert service.reconcile_expired_faults(redis) == 0
    assert snapshot(factory, new['id'])['fault_status'] == 'ACTIVE'
    service.log_fault_recovery('session-a', 'worker_unavailable', redis_client=redis)
    assert snapshot(factory, old['id'])['recovery_status'] == 'SUPERSEDED'
    assert snapshot(factory, new['id'])['recovery_status'] == 'MANUAL_RECOVERED'


def test_live_redis_key_and_failed_reads_are_not_recovery(lifecycle):
    factory, redis = lifecycle
    entry = inject(redis)
    redis.keys['ratelimit:fault:session-a:worker_unavailable'] = ('1', redis.now + 30)
    redis.now += 11
    assert service.reconcile_expired_faults(redis) == 0
    assert snapshot(factory, entry['id'])['fault_status'] == 'ACTIVE'
    redis.unavailable = True
    with pytest.raises(ConnectionError):
        service.reconcile_expired_faults(redis)
    assert snapshot(factory, entry['id'])['fault_status'] == 'ACTIVE'
    redis.unavailable = False
    redis.now += 20
    assert service.reconcile_expired_faults(redis) == 1


def test_background_restart_recovers_without_browser_request(lifecycle, monkeypatch):
    factory, redis = lifecycle
    import app.faults as runner
    monkeypatch.setattr(runner, 'client', lambda: redis)
    first_app = create_app({'TESTING': True})
    entry = inject(redis)
    stop = start_fault_reconciler(first_app, interval=.02)
    stop.set()
    first_app.extensions['fault_reconciler']['thread'].join(timeout=2)
    redis.now += 11  # Expires while the backend is stopped.
    assert snapshot(factory, entry['id'])['fault_status'] == 'ACTIVE'
    second_app = create_app({'TESTING': True})
    stop = start_fault_reconciler(second_app, interval=.02)
    try:
        deadline = time.monotonic() + 2
        while snapshot(factory, entry['id'])['fault_status'] == 'ACTIVE' and time.monotonic() < deadline:
            time.sleep(.01)
        assert snapshot(factory, entry['id'])['recovery_status'] == 'AUTO_RECOVERED'
        assert recoveries(factory) == 1
    finally:
        stop.set()
        second_app.extensions['fault_reconciler']['thread'].join(timeout=2)


def test_routes_expose_recovery_and_restore_all_is_session_scoped(lifecycle):
    factory, redis = lifecycle
    app = create_app({'TESTING': True, 'FAULTS_ENABLED': True})
    client = app.test_client()
    sid = generate_session_id()
    client.set_cookie('demo_session_id', sid)
    for name in ['worker_unavailable', 'latency_ms']:
        response = client.post('/api/faults', json={'name': name, 'duration_seconds': 10, 'value': 125})
        assert response.status_code == 200
    other = inject(redis, sid='other-session')
    assert client.delete('/api/faults').status_code == 200
    assert client.delete('/api/faults').status_code == 200
    rows = client.get('/api/logs/faults').json['items']
    assert len(rows) == 2
    assert all(row['recovery_status'] == 'MANUAL_RECOVERED' and row['recovery_timestamp'] for row in rows)
    assert snapshot(factory, other['id'])['fault_status'] == 'ACTIVE'
    assert recoveries(factory) == 2


def test_history_api_exposes_expiry_but_leaves_another_session_untouched(lifecycle):
    factory, redis = lifecycle
    client = create_app({'TESTING': True}).test_client()
    sid = generate_session_id()
    client.set_cookie('demo_session_id', sid)
    mine = inject(redis, sid=sid)
    other = inject(redis, sid='other-session')
    redis.now += 11
    response = client.get('/api/logs/faults')
    assert response.status_code == 200
    assert response.json['items'][0]['id'] == mine['id']
    assert response.json['items'][0]['recovery_status'] == 'AUTO_RECOVERED'
    assert snapshot(factory, other['id'])['fault_status'] == 'ACTIVE'
