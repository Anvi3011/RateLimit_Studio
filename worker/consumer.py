"""Commit PostgreSQL before ACK; retain deliveries on infrastructure failures."""
import json
import os
import signal
import time
import pika
import redis
from sqlalchemy.exc import DataError, IntegrityError
from processor import process
from db import init_db

stopping = False


def stop(*_):
    global stopping
    stopping = True


def handle_delivery(channel, method, properties, body, r):
    try:
        event = json.loads(body)
        if not isinstance(event, dict) or not event.get('event_id'):
            raise ValueError('event_id required')
    except (ValueError, TypeError, UnicodeError):
        channel.basic_nack(method.delivery_tag, requeue=False)
        return
    try:
        process(event, r)
    except (DataError, IntegrityError, ValueError, TypeError, OverflowError):
        channel.basic_nack(method.delivery_tag, requeue=False)
        return  # Poison message: durable dead-letter queue.
    except Exception:
        channel.basic_nack(method.delivery_tag, requeue=True)
        try:
            r.incr('ratelimit:worker:failures')
        except Exception:
            pass
        raise  # Reconnect with backoff, avoiding a hot requeue loop.
    channel.basic_ack(method.delivery_tag)


def run():
    r = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
                            decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
    interval = max(1, int(os.getenv('HEARTBEAT_INTERVAL', '5')))
    ttl = max(60, interval * 4)
    delay = 2
    while not stopping:
        conn = None
        try:
            if os.getenv('SCHEMA_INIT', 'true') == 'true':
                init_db()
            params = pika.URLParameters(os.getenv('RABBITMQ_URL', 'amqp://localhost:5672/%2F'))
            params.socket_timeout = 5
            params.stack_timeout = 10
            params.blocked_connection_timeout = 10
            params.heartbeat = 30
            params.connection_attempts = 1
            conn = pika.BlockingConnection(params)
            ch = conn.channel()
            ch.exchange_declare(exchange='ratelimit.dlx', exchange_type='direct', durable=True)
            ch.queue_declare(queue='ratelimit.events', durable=True,
                             arguments={'x-dead-letter-exchange': 'ratelimit.dlx'})
            ch.queue_declare(queue='ratelimit.events.dead', durable=True)
            ch.queue_bind(queue='ratelimit.events.dead', exchange='ratelimit.dlx', routing_key='ratelimit.events')
            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(queue='ratelimit.events', auto_ack=False,
                            on_message_callback=lambda c, m, p, b: handle_delivery(c, m, p, b, r))
            next_heartbeat = 0
            connected_at = time.monotonic()
            while not stopping and conn.is_open:
                conn.process_data_events(time_limit=1)
                if time.monotonic() >= next_heartbeat:
                    r.set('ratelimit:worker:last_seen', '1', ex=ttl)
                    next_heartbeat = time.monotonic() + interval
                if time.monotonic() - connected_at > 60:
                    delay = 2
        except Exception as exc:
            print(f'worker unavailable ({type(exc).__name__}); retry in {delay}s', flush=True)
            until = time.monotonic() + delay
            while not stopping and time.monotonic() < until:
                time.sleep(.2)
            delay = min(30, delay * 2)
        finally:
            if conn is not None and conn.is_open:
                try:
                    conn.close()
                except Exception:
                    pass


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run()
