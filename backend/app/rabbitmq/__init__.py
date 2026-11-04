import json, os, uuid
import pika

QUEUE='ratelimit.events'
def publish(event):
    event.setdefault('event_id',str(uuid.uuid4()))
    params=pika.URLParameters(os.getenv('RABBITMQ_URL','amqp://localhost:5672/%2F')); params.socket_timeout=1
    conn=pika.BlockingConnection(params)
    try:
        ch=conn.channel(); ch.queue_declare(queue=QUEUE,durable=True,arguments={'x-dead-letter-exchange':'ratelimit.dlx'})
        ch.exchange_declare(exchange='ratelimit.dlx',exchange_type='direct',durable=True)
        ch.queue_declare(queue=QUEUE+'.dead',durable=True); ch.queue_bind(queue=QUEUE+'.dead',exchange='ratelimit.dlx',routing_key=QUEUE)
        ch.confirm_delivery()
        confirmed=ch.basic_publish(exchange='',routing_key=QUEUE,body=json.dumps(event),properties=pika.BasicProperties(delivery_mode=2,content_type='application/json',message_id=event['event_id']))
        if not confirmed: raise RuntimeError('RabbitMQ did not confirm the event')
    finally: conn.close()
