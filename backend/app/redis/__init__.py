import hashlib, os, time
import redis
from ..algorithms.scripts import TOKEN_BUCKET, SLIDING_WINDOW

_client = None
def client():
    global _client
    if _client is None: _client = redis.Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'), socket_connect_timeout=float(os.getenv('REDIS_CONNECT_TIMEOUT', '.3')), socket_timeout=float(os.getenv('REDIS_SOCKET_TIMEOUT', '.5')), decode_responses=True)
    return _client

def key(client_id, endpoint, policy_id):
    client_bytes=client_id.encode(); endpoint_bytes=endpoint.encode()
    digest=hashlib.sha256(len(client_bytes).to_bytes(4,'big')+client_bytes+endpoint_bytes).hexdigest()
    return f'rl:v1:{policy_id}:{digest}'

def decide(client_id, endpoint, policy, edge_id):
    r = client()
    start = time.perf_counter()
    base = key(client_id, endpoint, policy['policy_id'])
    rate = policy['rate']
    if policy['algorithm'] == 'token_bucket':
        burst = policy['burst']
        state_key = f'{base}:token_bucket:{rate}:{burst}'
        out = r.eval(TOKEN_BUCKET, 1, state_key, '', rate, burst, 1,
                     max(2, int(burst / rate * 2) + 1))
    else:
        window = policy['window_seconds']
        state_key = f'{base}:sliding_window:{rate}:{window}'
        member = f'{edge_id}:{os.urandom(16).hex()}'
        out = r.eval(SLIDING_WINDOW, 1, state_key, '', window, rate * window, member)
    return {'allowed': bool(int(out[0])), 'remaining': int(out[1]), 'retry_after': int(out[2]),
            'redis_latency_ms': (time.perf_counter() - start) * 1000}
