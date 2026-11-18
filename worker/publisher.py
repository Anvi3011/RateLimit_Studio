"""Bounded Redis outbox relay. RabbitMQ confirms before the inflight record is removed."""
import json, os, signal, time
import pika, redis

OUTBOX='ratelimit:outbox'
INFLIGHT=OUTBOX+':inflight'
FAILED=OUTBOX+':failed'
CAPACITY=max(1,min(100000,int(os.getenv('OUTBOX_CAPACITY','5000'))))
MAX_ATTEMPTS=8
FAIL_ITEM_SCRIPT="""
local removed=redis.call('LREM',KEYS[1],1,ARGV[1]); if removed==0 then return 'missing' end
if tonumber(ARGV[3])>=tonumber(ARGV[5]) then
  redis.call('LPUSH',KEYS[3],ARGV[2]); redis.call('LTRIM',KEYS[3],0,999)
  redis.call('HINCRBY',KEYS[4],'publish_failures',1); return 'failed'
end
if redis.call('LLEN',KEYS[2])>=tonumber(ARGV[4]) then
  redis.call('LPUSH',KEYS[3],ARGV[2]); redis.call('LTRIM',KEYS[3],0,999)
  redis.call('HINCRBY',KEYS[4],'publish_failures',1); redis.call('HINCRBY',KEYS[4],'outbox_overflow',1); return 'failed'
end
redis.call('RPUSH',KEYS[2],ARGV[2]); redis.call('HINCRBY',KEYS[4],'publisher_retries',1); return 'retry'
"""
stopping=False

def stop(*_):
    global stopping
    stopping=True


def publish(event, connection=None):
    params=pika.URLParameters(os.getenv('RABBITMQ_URL','amqp://localhost:5672/%2F')); params.socket_timeout=5; params.stack_timeout=10; params.blocked_connection_timeout=10; params.heartbeat=30; params.connection_attempts=1
    conn=connection or pika.BlockingConnection(params)
    ch=None
    try:
        ch=conn.channel(); ch.exchange_declare(exchange='ratelimit.dlx',exchange_type='direct',durable=True)
        ch.queue_declare(queue='ratelimit.events',durable=True,arguments={'x-dead-letter-exchange':'ratelimit.dlx'})
        ch.queue_declare(queue='ratelimit.events.dead',durable=True); ch.queue_bind(queue='ratelimit.events.dead',exchange='ratelimit.dlx',routing_key='ratelimit.events')
        ch.confirm_delivery()
        # BlockingChannel.basic_publish returns None after an ACK in Pika 1.3.x.
        # With confirms enabled, broker NACKs and unroutable mandatory messages
        # raise NackError/UnroutableError, so successful return is the ACK path.
        ch.basic_publish(exchange='',routing_key='ratelimit.events',body=json.dumps(event),properties=pika.BasicProperties(delivery_mode=2,content_type='application/json',message_id=event['event_id']),mandatory=True)
    finally:
        if connection is None: conn.close()
        elif ch is not None and ch.is_open: ch.close()

def fail_item(r, raw, event):
    event['publish_attempts']=int(event.get('publish_attempts',0))+1
    updated=json.dumps(event,separators=(',',':'))
    result=r.eval(FAIL_ITEM_SCRIPT,4,INFLIGHT,OUTBOX,FAILED,'ratelimit:metrics',raw,updated,event['publish_attempts'],CAPACITY,MAX_ATTEMPTS)
    sid=event.get('demo_session_id')
    if sid and result in ('failed','retry'):
        r.hincrby('ratelimit:metrics:'+sid, 'publish_failures' if result=='failed' else 'publisher_retries', 1)
    if result=='failed': print('outbox item moved to bounded failed queue',flush=True)

def recover_inflight(r, renew=None):
    """Move unconfirmed messages back to the bounded retry list after a crash."""
    recovered=0
    while True:
        if renew: renew()
        raw=r.rpoplpush(INFLIGHT,OUTBOX)
        if raw is None: return recovered
        recovered+=1

def pause(seconds):
    until = time.monotonic() + seconds
    while not stopping and time.monotonic() < until:
        time.sleep(.2)


def run():
    wait = max(1, min(30, int(os.getenv('PUBLISHER_BLOCK_SECONDS', '1'))))
    interval = max(1, int(os.getenv('HEARTBEAT_INTERVAL', '5')))
    r = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
                             decode_responses=True, socket_connect_timeout=5, socket_timeout=wait + 10)
    # A rolling Render deploy may briefly overlap old/new instances. Only one
    # relay owns inflight recovery. Every operation is bounded below this lease.
    lease = r.lock('ratelimit:publisher:lease', timeout=120, blocking_timeout=0)
    # A hard-killed predecessor retains its lease until expiry. Wait in this
    # process instead of triggering the supervisor's short-failure crash limit.
    while not stopping and not lease.acquire(blocking=False):
        pause(5)
    if stopping:
        return
    conn = None
    next_heartbeat = 0
    next_renew = 0

    def renew():
        nonlocal next_renew
        if time.monotonic() >= next_renew:
            lease.extend(120, replace_ttl=True)
            next_renew = time.monotonic() + 30

    try:
        recover_inflight(r, renew)
        while not stopping:
            renew()  # Lost ownership aborts before moving another event.
            if time.monotonic() >= next_heartbeat:
                r.set('ratelimit:publisher:last_seen', '1', ex=max(90, interval * 4))
                next_heartbeat = time.monotonic() + interval
            raw = r.brpoplpush(OUTBOX, INFLIGHT, timeout=wait)
            if raw is None:
                if conn is not None and conn.is_open:
                    conn.process_data_events(time_limit=0)
                continue
            try:
                event = json.loads(raw)
                if conn is None or conn.is_closed:
                    params = pika.URLParameters(os.getenv('RABBITMQ_URL', 'amqp://localhost:5672/%2F'))
                    params.socket_timeout = 5
                    params.stack_timeout = 10
                    params.blocked_connection_timeout = 10
                    params.heartbeat = 60
                    params.connection_attempts = 1
                    conn = pika.BlockingConnection(params)
                publish(event, connection=conn)
                r.lrem(INFLIGHT, 1, raw)
                r.hincrby('ratelimit:metrics', 'events_published', 1)
                sid = event.get('demo_session_id')
                if sid:
                    r.hincrby('ratelimit:metrics:' + sid, 'events_published', 1)
            except Exception as exc:
                if conn is not None and conn.is_open:
                    conn.close()
                conn = None
                try:
                    event = json.loads(raw)
                    fail_item(r, raw, event)
                    print(f'publish failed ({type(exc).__name__})', flush=True)
                    pause(min(30, 2 ** int(event['publish_attempts'])))
                except Exception:
                    # Keep inflight for recovery rather than silently losing it.
                    raise RuntimeError('Publisher recovery required') from None
    finally:
        if conn is not None and conn.is_open:
            conn.close()
        try:
            lease.release()
        except redis.exceptions.LockError:
            pass

if __name__=='__main__':
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    run()
