import os
import redis

def process(event, r=None):
    r=r or redis.Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True)
    marker='ratelimit:event:processed:'+str(event['event_id'])
    if not r.set(marker,'1',nx=True,ex=604800): return False
    r.hincrby('ratelimit:worker:by_decision',event.get('decision','unknown'),1)
    r.incr('ratelimit:worker:processed')
    return True
