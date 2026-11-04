import hashlib, os, time
import redis
from ..algorithms.scripts import TOKEN_BUCKET, SLIDING_WINDOW

_client = None
def client():
    global _client
    if _client is None: _client = redis.Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'), socket_connect_timeout=.3, socket_timeout=.5, decode_responses=True)
    return _client

def key(client_id, endpoint, policy_id):
    client_bytes=client_id.encode(); endpoint_bytes=endpoint.encode()
    digest=hashlib.sha256(len(client_bytes).to_bytes(4,'big')+client_bytes+endpoint_bytes).hexdigest()
    return f'rl:v1:{policy_id}:{digest}'

def decide(client_id, endpoint, policy, edge_id):
    r=client(); start=time.perf_counter(); k=key(client_id,endpoint,policy['policy_id']); now=int(time.time()*1000)
    if policy['algorithm']=='token_bucket':
        out=r.eval(TOKEN_BUCKET,1,k,now,policy['rate'],policy['burst'],1,max(2,int(policy['burst']/policy['rate']*2)+1))
    else:
        out=r.eval(SLIDING_WINDOW,1,k,now,policy['window_seconds'],policy['rate']*policy['window_seconds'],f'{now}:{edge_id}:{os.urandom(8).hex()}')
    return {'allowed':bool(int(out[0])),'remaining':int(out[1]),'retry_after':int(out[2]),'redis_latency_ms':(time.perf_counter()-start)*1000}
