import pytest
import processor

def test_database_session_failure_does_not_acknowledge_event(monkeypatch):
    class Store:
        def __init__(self): self.marker = False; self.counters = 0
        def set(self, *args, **kwargs): self.marker = True; return True
        def delete(self, *args): self.marker = False
        def hincrby(self, *args): self.counters += 1
        def incr(self, *args): self.counters += 1
    def unavailable(): raise ConnectionError('database unavailable')
    monkeypatch.setattr(processor, 'get_session', unavailable)
    store = Store()
    with pytest.raises(ConnectionError):
        processor.process({'event_id': 'db-unavailable', 'decision': 'accepted'}, store)
    assert store.marker is False
    assert store.counters == 0
