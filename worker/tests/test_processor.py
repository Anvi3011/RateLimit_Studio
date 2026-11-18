import pytest
import fakeredis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import processor
from processor import process
from db import Base, RequestLog


@pytest.fixture
def db_session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def event():
    return {'event_id': 'event-1', 'request_id': 'req-1', 'decision': 'accepted',
            'demo_session_id': 'session-test', 'status': 200}


def test_duplicate_has_one_durable_row_and_one_counter(db_session, event):
    r = fakeredis.FakeRedis(decode_responses=True)
    assert process(event, r, db_session) is True
    assert process(event, r, db_session) is False
    assert db_session.query(RequestLog).count() == 1
    assert r.get('ratelimit:worker:processed') == '1'


def test_legacy_marker_cannot_suppress_missing_row(db_session, event):
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set('ratelimit:event:processed:event-1', '1', ex=604800)
    assert process(event, r, db_session) is True
    assert db_session.query(RequestLog).count() == 1


def test_obtaining_db_session_failure_propagates(monkeypatch, event):
    def unavailable():
        raise ConnectionError('test database unavailable')
    monkeypatch.setattr(processor, 'get_session', unavailable)
    with pytest.raises(ConnectionError):
        process(event, fakeredis.FakeRedis())


def test_commit_failure_does_not_create_marker(db_session, event, monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    def unavailable():
        raise ConnectionError('test commit failed')
    monkeypatch.setattr(db_session, 'commit', unavailable)
    with pytest.raises(ConnectionError):
        process(event, r, db_session)
    assert db_session.query(RequestLog).count() == 0
    assert not r.exists('ratelimit:event:processed:event-1')


def test_crash_after_commit_before_marker_is_recoverable(db_session, event, monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    original = r.eval
    def unavailable(*_):
        raise ConnectionError('test Redis outage after commit')
    monkeypatch.setattr(r, 'eval', unavailable)
    with pytest.raises(ConnectionError):
        process(event, r, db_session)
    assert db_session.query(RequestLog).count() == 1
    monkeypatch.setattr(r, 'eval', original)
    assert process(event, r, db_session) is False
    assert db_session.query(RequestLog).count() == 1
    assert r.get('ratelimit:worker:processed') == '1'


def test_expired_marker_never_duplicates_database_row(db_session, event):
    r = fakeredis.FakeRedis(decode_responses=True)
    process(event, r, db_session)
    r.delete('ratelimit:event:processed:event-1')
    assert process(event, r, db_session) is False
    assert db_session.query(RequestLog).count() == 1
    # Worker telemetry is seven-day scoped, not the durable audit count.
    assert r.get('ratelimit:worker:processed') == '2'

@pytest.mark.parametrize(('status', 'decision', 'stored'), [
    (200, 'accepted', 'ALLOWED'), (429, 'rejected', 'REJECTED'),
    (503, 'rejected', 'ERROR'), (429, 'unknown', 'ERROR'),
])
def test_persisted_classification_requires_http_and_decision_agreement(db_session, event, status, decision, stored):
    r = fakeredis.FakeRedis(decode_responses=True)
    assert process(dict(event, status=status, decision=decision), r, db_session)
    row = db_session.query(RequestLog).one()
    assert (row.response_status, row.rate_limit_decision) == (status, stored)
