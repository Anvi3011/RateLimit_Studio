import os, sys, redis
try:
    r=redis.Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379/0'),socket_connect_timeout=1)
    sys.exit(0 if r.ttl('ratelimit:worker:last_seen')>0 else 1)
except Exception: sys.exit(1)
