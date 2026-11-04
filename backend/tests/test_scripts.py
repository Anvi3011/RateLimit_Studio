from app.algorithms.scripts import TOKEN_BUCKET, SLIDING_WINDOW

def test_lua_scripts_have_atomic_expiring_operations():
    assert 'HSET' in TOKEN_BUCKET and 'EXPIRE' in TOKEN_BUCKET
    assert 'ZREMRANGEBYSCORE' in SLIDING_WINDOW and 'ZADD' in SLIDING_WINDOW

def test_keys_are_collision_resistant():
    from app.redis import key
    assert key('a','bc','p') != key('ab','c','p')
    assert key('a','bc','p').startswith('rl:v1:p:')
