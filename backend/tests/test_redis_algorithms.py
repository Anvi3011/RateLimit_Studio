from concurrent.futures import ThreadPoolExecutor
import os
import uuid

import pytest
import redis

from app.algorithms.scripts import TOKEN_BUCKET, SLIDING_WINDOW
from app.redis import key

@pytest.fixture(scope='module')
def r():
    try:
        client=redis.Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True)
        client.ping()
        return client
    except Exception:
        import fakeredis
        return fakeredis.FakeRedis(decode_responses=True)

def test_token_bucket_capacity_and_controlled_refill(r):
    k=key('test-client','/bucket','test-'+uuid.uuid4().hex)
    try:
        decisions=[r.eval(TOKEN_BUCKET,1,k,100000,2,4,1,30)[0] for _ in range(5)]
        assert decisions==[1,1,1,1,0]
        allowed,remaining,retry=r.eval(TOKEN_BUCKET,1,k,100500,2,4,1,30)
        assert (allowed,remaining,retry)==(1,0,0)
    finally: r.delete(k)

def test_sliding_window_expires_on_exact_boundary(r):
    k='rl:test:window:'+uuid.uuid4().hex
    try:
        first=[r.eval(SLIDING_WINDOW,1,k,100000,1,2,f'a{i}')[0] for i in range(3)]
        assert first==[1,1,0]
        assert r.eval(SLIDING_WINDOW,1,k,101000,1,2,'boundary')[0]==1
    finally: r.delete(k)

def test_concurrent_bucket_requests_never_exceed_capacity(r):
    k='rl:test:concurrent:'+uuid.uuid4().hex
    def take(i): return int(r.eval(TOKEN_BUCKET,1,k,200000,1,10,1,30)[0])
    try:
        with ThreadPoolExecutor(max_workers=24) as pool: outcomes=list(pool.map(take,range(80)))
        assert sum(outcomes)==10
    finally: r.delete(k)

def test_concurrent_sliding_requests_never_exceed_window_limit(r):
    k='rl:test:concurrent-window:'+uuid.uuid4().hex
    def take(i): return int(r.eval(SLIDING_WINDOW,1,k,300000,5,12,str(i))[0])
    try:
        with ThreadPoolExecutor(max_workers=24) as pool: outcomes=list(pool.map(take,range(80)))
        assert sum(outcomes)==12
    finally: r.delete(k)

def test_client_and_endpoint_keys_are_isolated():
    policy='test-'+uuid.uuid4().hex
    assert key('ab','c',policy)!=key('a','bc',policy)
    assert key('client','/one',policy)!=key('client','/two',policy)
