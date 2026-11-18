"""Run only in compose.integration.yml: real Redis/RabbitMQ/PostgreSQL, no mocks."""
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import pika
import redis
from sqlalchemy import create_engine, text
from processor import process


def browser():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(opener, path, method='GET', data=None):
    request = urllib.request.Request('http://demo:10000/api' + path, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    try:
        response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    return response.status, json.loads(response.read())


def eventually(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.25)
    raise AssertionError('Timed out waiting for real pipeline persistence')


def check_lua(r):
    from app.algorithms.scripts import SLIDING_WINDOW, TOKEN_BUCKET

    key = 'rl:integration:' + uuid.uuid4().hex
    bucket_key = key + ':bucket'
    window_key = key + ':window'
    with ThreadPoolExecutor(max_workers=8) as pool:
        bucket = list(pool.map(
            lambda _: r.eval(TOKEN_BUCKET, 1, bucket_key, 100000, 2, 4, 1, 30)[0], range(16)))
        window = list(pool.map(
            lambda i: r.eval(SLIDING_WINDOW, 1, window_key, 100000, 1, 4, str(i))[0], range(16)))
    assert sum(bucket) == sum(window) == 4
    assert r.eval(TOKEN_BUCKET, 1, bucket_key, 100499, 2, 4, 1, 30)[0] == 0
    assert r.eval(TOKEN_BUCKET, 1, bucket_key, 100500, 2, 4, 1, 30)[0] == 1
    assert r.eval(SLIDING_WINDOW, 1, window_key, 100999, 1, 4, 'early')[0] == 0
    assert r.eval(SLIDING_WINDOW, 1, window_key, 101000, 1, 4, 'boundary')[0] == 1
    assert r.ttl(bucket_key) > 0 and r.ttl(window_key) > 0
    print('PASS: controlled-clock Lua concurrency, refill, rolling window boundary and TTL on real Redis', flush=True)


def main():
    engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
    from app.db import get_engine as backend_engine
    from db import get_engine as worker_engine
    for factory in (backend_engine, worker_engine):
        with factory().connect() as connection:
            assert connection.exec_driver_sql('SHOW statement_timeout').scalar() == '10s'
    r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    check_lua(r)
    a, b = browser(), browser()
    sid_a = call(a, '/session')[1]['session_id']
    sid_b = call(b, '/session')[1]['session_id']
    assert sid_a != sid_b
    assert call(a, '/health')[1]['database'] == 'healthy'
    original = []
    client_id = str(uuid.uuid4())
    for algorithm in ('token_bucket', 'sliding_window'):
        assert call(a, '/config', 'PUT', {'algorithm': algorithm, 'rate': 1, 'burst': 1,
                                        'window_seconds': 10})[0] == 200
        statuses = []
        for _ in range(12):
            status, event = call(a, '/requests/test', 'POST', {'client_id': client_id})
            statuses.append(status)
            original.append(event)
        assert 200 in statuses and 429 in statuses
    assert call(b, '/logs/requests')[1]['total'] == 0

    def all_saved():
        with engine.connect() as conn:
            return conn.execute(text('SELECT count(*) FROM request_logs WHERE demo_session_id=:sid'),
                                {'sid': sid_a}).scalar() == len(original)
    eventually(all_saved)
    logs = call(a, '/logs/requests')[1]
    assert logs['total'] == 24
    by_id = {row['request_id']: row for row in logs['items']}
    for event in original:
        row = by_id[event['request_id']]
        assert (row['algorithm'], row['response_status'], row['rate_limit_decision']) == (
            event['algorithm'], event['status'], event['rate_limit_decision'])
    counts = call(a, '/logs/summary')[1]
    assert counts['allowed_requests'] == sum(event['status'] == 200 for event in original)
    assert counts['rejected_requests'] == sum(event['status'] == 429 for event in original)
    assert counts['failed_operations'] == 0
    assert call(a, '/logs/configurations')[1]['total'] == 2
    assert any(event['event_type'] == 'CONFIGURATION_APPLIED'
               for event in call(a, '/logs/system-events')[1]['items'])
    assert call(b, '/logs/configurations')[1]['total'] == 0
    assert call(a, '/metrics')[1]['total'] == 24
    assert any(p['requests'] for p in call(a, '/metrics/timeseries')[1]['points'])

    # Broker redelivery after commit-before-ACK must not duplicate SQL history.
    conn = pika.BlockingConnection(pika.URLParameters(os.environ['RABBITMQ_URL']))
    ch = conn.channel()
    ch.confirm_delivery()
    for _ in range(3):
        ch.basic_publish('', 'ratelimit.events', json.dumps(original[0]), mandatory=True,
                         properties=pika.BasicProperties(delivery_mode=2))
    # A legacy marker without a row must be repaired on delivery.
    repaired = dict(original[0], event_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
    r.set('ratelimit:event:processed:' + repaired['event_id'], '1', ex=604800)
    ch.basic_publish('', 'ratelimit.events', json.dumps(repaired), mandatory=True)
    conn.close()
    eventually(lambda: call(a, '/logs/requests')[1]['total'] == 25)

    # Exercise real PostgreSQL unique conflicts under concurrent consumers.
    concurrent = dict(original[0], event_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
    with ThreadPoolExecutor(max_workers=6) as pool:
        inserted = list(pool.map(lambda _: process(concurrent, r), range(6)))
    assert sum(inserted) == 1
    assert call(a, '/logs/requests')[1]['total'] == 26
    assert call(b, '/logs/requests')[1]['total'] == 0
    print('PASS: real pipeline, both Lua algorithms, SQL history, dashboard analytics, sessions, broker redelivery, legacy-marker repair, concurrent SQL idempotency', flush=True)


if __name__ == '__main__':
    main()
