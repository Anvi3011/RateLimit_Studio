import json, os, signal, time
import pika
import redis
from processor import process

stopping=False
def stop(*_):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
url=os.getenv('RABBITMQ_URL','amqp://localhost:5672/%2F')
r=redis.Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True)
while not stopping:
    try:
        params=pika.URLParameters(url); conn=pika.BlockingConnection(params); ch=conn.channel()
        ch.exchange_declare(exchange='ratelimit.dlx',exchange_type='direct',durable=True)
        ch.queue_declare(queue='ratelimit.events',durable=True,arguments={'x-dead-letter-exchange':'ratelimit.dlx'})
        ch.queue_declare(queue='ratelimit.events.dead',durable=True); ch.queue_bind(queue='ratelimit.events.dead',exchange='ratelimit.dlx',routing_key='ratelimit.events')
        ch.basic_qos(prefetch_count=32)
        def callback(channel,method,properties,body):
            try: process(json.loads(body),r); channel.basic_ack(method.delivery_tag)
            except Exception as exc:
                r.incr('ratelimit:worker:failures'); print(f'event processing failed: {exc}',flush=True); channel.basic_nack(method.delivery_tag,requeue=False)
        consumer_tag=None
        while not stopping and conn.is_open:
            unavailable=bool(r.get('ratelimit:fault:worker_unavailable'))
            if unavailable and consumer_tag:
                ch.basic_cancel(consumer_tag); consumer_tag=None
            elif not unavailable and not consumer_tag:
                consumer_tag=ch.basic_consume(queue='ratelimit.events',on_message_callback=callback,auto_ack=False)
            conn.process_data_events(time_limit=1)
            if unavailable: r.delete('ratelimit:worker:last_seen')
            else: r.set('ratelimit:worker:last_seen','1',ex=15)
        if conn.is_open: conn.close()
    except Exception as exc:
        print(f'worker connection unavailable: {exc}',flush=True); time.sleep(2)
