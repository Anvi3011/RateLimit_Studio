import json
from types import SimpleNamespace

import publisher
from publisher import FAILED, INFLIGHT, OUTBOX, fail_item, publish, recover_inflight


class MemoryRedis:
    def __init__(self):
        self.lists = {OUTBOX: [], INFLIGHT: [], FAILED: []}
        self.hash = {}

    def lrem(self, key, _count, value):
        if value in self.lists[key]: self.lists[key].remove(value)
    def llen(self, key): return len(self.lists.setdefault(key, []))
    def rpush(self, key, value): self.lists.setdefault(key, []).append(value)
    def lpush(self, key, value): self.lists.setdefault(key, []).insert(0, value)
    def ltrim(self, key, start, end): self.lists[key] = self.lists[key][start:end + 1]
    def hincrby(self, key, field, amount): self.hash[(key, field)] = self.hash.get((key, field), 0) + amount
    def rpoplpush(self, source, destination):
        values = self.lists.setdefault(source, [])
        if not values: return None
        value = values.pop(); self.lists.setdefault(destination, []).insert(0, value); return value
    def eval(self, _script, key_count, *args):
        inflight, outbox, failed, metrics = args[:key_count]
        raw, updated, attempts, capacity, max_attempts = args[key_count:]
        if raw not in self.lists[inflight]: return 'missing'
        self.lists[inflight].remove(raw)
        is_full = len(self.lists[outbox]) >= int(capacity)
        if int(attempts) >= int(max_attempts) or is_full:
            self.lists[failed].insert(0, updated); self.lists[failed] = self.lists[failed][:1000]
            field='publish_failures'
            if is_full and int(attempts) < int(max_attempts): self.hash[(metrics, 'outbox_overflow')] = self.hash.get((metrics, 'outbox_overflow'), 0) + 1
            result='failed'
        else:
            self.lists[outbox].append(updated); field='publisher_retries'; result='retry'
        self.hash[(metrics, field)] = self.hash.get((metrics, field), 0) + 1
        return result


def test_failed_publish_is_requeued_with_incremented_attempt_count():
    r = MemoryRedis(); item = {'event_id': 'id-1'}; raw = json.dumps(item); r.lists[INFLIGHT].append(raw)
    fail_item(r, raw, item)
    saved = json.loads(r.lists[OUTBOX][0])
    assert saved['publish_attempts'] == 1
    assert r.llen(INFLIGHT) == 0
    assert r.hash[('ratelimit:metrics', 'publisher_retries')] == 1


def test_retry_exhaustion_moves_message_to_bounded_failed_queue():
    r = MemoryRedis(); item = {'event_id': 'id-2', 'request_id': 'req-2', 'decision': 'accepted', 'publish_attempts': 7}; raw = json.dumps(item); r.lists[INFLIGHT].append(raw)
    fail_item(r, raw, item)
    saved = json.loads(r.lists[FAILED][0])
    assert saved['publish_attempts'] == 8
    assert (saved['event_id'], saved['request_id'], saved['decision']) == ('id-2', 'req-2', 'accepted')
    assert r.llen(OUTBOX) == 0
    assert r.hash[('ratelimit:metrics', 'publish_failures')] == 1
    assert r.hash.get(('ratelimit:metrics', 'outbox_overflow'), 0) == 0


def test_restart_recovery_moves_unconfirmed_events_back_to_outbox():
    r = MemoryRedis(); r.lists[INFLIGHT].extend(['event-a', 'event-b'])
    assert recover_inflight(r) == 2
    assert r.llen(INFLIGHT) == 0
    assert set(r.lists[OUTBOX]) == {'event-a', 'event-b'}


def test_full_retry_list_moves_event_to_failed_queue(monkeypatch):
    monkeypatch.setattr(publisher, 'CAPACITY', 1)
    r = MemoryRedis(); r.lists[OUTBOX].append('already-full')
    item = {'event_id': 'id-3'}; raw = json.dumps(item); r.lists[INFLIGHT].append(raw)
    fail_item(r, raw, item)
    assert r.llen(OUTBOX) == 1
    assert json.loads(r.lists[FAILED][0])['publish_attempts'] == 1
    assert r.hash[('ratelimit:metrics', 'outbox_overflow')] == 1


def test_failed_queue_is_capped_at_one_thousand_entries():
    r = MemoryRedis(); r.lists[FAILED] = [f'old-{i}' for i in range(1000)]
    item = {'event_id': 'id-4', 'publish_attempts': 7}; raw = json.dumps(item); r.lists[INFLIGHT].append(raw)
    fail_item(r, raw, item)
    assert r.llen(FAILED) == 1000


def test_pika_successful_confirm_with_none_return_is_accepted(monkeypatch):
    class Channel:
        def __init__(self): self.confirmed = False; self.message = None
        def exchange_declare(self, **_): pass
        def queue_declare(self, **_): pass
        def queue_bind(self, **_): pass
        def confirm_delivery(self): self.confirmed = True
        def basic_publish(self, **kwargs): self.message = kwargs; return None

    channel = Channel()
    class Connection:
        def channel(self): return channel
        def close(self): pass

    monkeypatch.setattr(publisher.pika, 'URLParameters', lambda _url: SimpleNamespace(socket_timeout=None, connection_attempts=None))
    monkeypatch.setattr(publisher.pika, 'BlockingConnection', lambda _params: Connection())
    publish({'event_id': 'confirm-none'})
    assert channel.confirmed
    assert channel.message['mandatory'] is True
    assert channel.message['properties'].message_id == 'confirm-none'
