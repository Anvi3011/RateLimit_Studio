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


def publish(event):
    params=pika.URLParameters(os.getenv('RABBITMQ_URL','amqp://localhost:5672/%2F')); params.socket_timeout=3; params.connection_attempts=1
    conn=pika.BlockingConnection(params)
    try:
        ch=conn.channel(); ch.exchange_declare(exchange='ratelimit.dlx',exchange_type='direct',durable=True)
        ch.queue_declare(queue='ratelimit.events',durable=True,arguments={'x-dead-letter-exchange':'ratelimit.dlx'})
        ch.queue_declare(queue='ratelimit.events.dead',durable=True); ch.queue_bind(queue='ratelimit.events.dead',exchange='ratelimit.dlx',routing_key='ratelimit.events')
        ch.confirm_delivery()
        # BlockingChannel.basic_publish returns None after an ACK in Pika 1.3.x.
        # With confirms enabled, broker NACKs and unroutable mandatory messages
        # raise NackError/UnroutableError, so successful return is the ACK path.
        ch.basic_publish(exchange='',routing_key='ratelimit.events',body=json.dumps(event),properties=pika.BasicProperties(delivery_mode=2,content_type='application/json',message_id=event['event_id']),mandatory=True)
    finally: conn.close()

def fail_item(r, raw, event):
    event['publish_attempts']=int(event.get('publish_attempts',0))+1
    updated=json.dumps(event,separators=(',',':'))
    result=r.eval(FAIL_ITEM_SCRIPT,4,INFLIGHT,OUTBOX,FAILED,'ratelimit:metrics',raw,updated,event['publish_attempts'],CAPACITY,MAX_ATTEMPTS)
    if result=='failed': print('outbox item moved to bounded failed queue',flush=True)

def recover_inflight(r):
    """Move unconfirmed messages back to the bounded retry list after a crash."""
    recovered=0
    while True:
        raw=r.rpoplpush(INFLIGHT,OUTBOX)
        if raw is None: return recovered
        recovered+=1

def run():
    r=redis.Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True)
    # Recover deliveries left unconfirmed by a previous publisher process.
    recover_inflight(r)
    while not stopping:
        r.set('ratelimit:publisher:last_seen','1',ex=60)
        raw=r.brpoplpush(OUTBOX,INFLIGHT,timeout=1)
        if raw is None: continue
        try:
            event=json.loads(raw); publish(event)
            r.lrem(INFLIGHT,1,raw); r.hincrby('ratelimit:metrics','events_published',1)
        except Exception as exc:
            try:
                event=json.loads(raw); fail_item(r,raw,event)
                delay=min(30,2**int(event['publish_attempts'])) if int(event['publish_attempts'])<MAX_ATTEMPTS else 0
                print(f'publish failed ({type(exc).__name__}); retry {event["publish_attempts"]}/{MAX_ATTEMPTS} in {delay}s',flush=True)
                if delay: time.sleep(delay)
            except Exception as nested:
                print(f'publisher recovery failed ({type(nested).__name__}); inflight retained for restart recovery',flush=True); time.sleep(2)

if __name__=='__main__':
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    run()
