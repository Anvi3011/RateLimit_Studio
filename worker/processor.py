import os
import logging
from datetime import datetime, timezone
import redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from db import RequestLog, get_session, parse_dt

logger = logging.getLogger(__name__)

def save_to_db(event, db_session=None):
    session = db_session
    close_session = False
    if session is None:
        session = get_session()  # Failure must propagate: never ACK without a row.
        close_session = True

    try:
        req_id = str(event.get('request_id') or event.get('event_id'))
        req_ts = parse_dt(event.get('request_timestamp') or event.get('timestamp')) or datetime.now(timezone.utc)
        resp_ts = parse_dt(event.get('response_timestamp'))

        decision_raw = str(event.get('decision') or event.get('rate_limit_decision') or '').lower()
        status = int(event.get('status', event.get('response_status', 200)))
        if status == 200 and decision_raw in ('accepted', 'allowed'):
            decision = 'ALLOWED'
        elif status == 429 and decision_raw == 'rejected':
            decision = 'REJECTED'
        else:
            decision = 'ERROR'

        log_entry = RequestLog(
            request_id=req_id,
            correlation_id=event.get('correlation_id'),
            demo_session_id=str(event.get('demo_session_id') or event.get('policy_id') or 'default')[:64],
            client_id=str(event.get('client_id', 'anonymous'))[:128],
            algorithm=str(event.get('algorithm', 'token_bucket'))[:32],
            endpoint=str(event.get('endpoint', '/api/demo'))[:256],
            http_method=str(event.get('http_method', 'POST'))[:10],
            request_timestamp=req_ts,
            response_timestamp=resp_ts,
            response_status=status,
            rate_limit_decision=decision,
            rejection_reason=event.get('reason'),
            request_processing_time_ms=float(event.get('latency_ms', 0.0)),
            configured_rate_limit=event.get('configured_rate_limit'),
            burst_capacity=event.get('burst_capacity'),
            window_duration=event.get('window_duration'),
            edge_node_id=event.get('edge_id') or event.get('edge_node_id')
        )
        # Database uniqueness is the durable authority, including concurrent delivery.
        dialect = session.get_bind().dialect.name
        insert = pg_insert if dialect == 'postgresql' else sqlite_insert
        values = {c.name: getattr(log_entry, c.name) for c in RequestLog.__table__.columns
                  if c.name not in ('id', 'created_at')}
        result = session.execute(insert(RequestLog).values(**values).on_conflict_do_nothing(
            index_elements=['request_id']))
        session.commit()
        return result.rowcount == 1
    except Exception as e:
        session.rollback()
        logger.error("Request persistence failed (%s)", type(e).__name__)
        raise
    finally:
        if close_session:
            session.close()

def process(event, r=None, db_session=None):
    r = r or redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    marker = 'ratelimit:event:processed:' + str(event['event_id'])

    # Never skip PostgreSQL because a Redis marker exists (including legacy markers).
    inserted = save_to_db(event, db_session=db_session)
    # Atomic telemetry AFTER commit. If Redis fails, redelivery verifies the row
    # and retries telemetry. SQL uniqueness prevents a second history row.
    r.eval("""
        if redis.call('SET',KEYS[1],'1','NX','EX',604800) then
          redis.call('HINCRBY',KEYS[2],ARGV[1],1)
          redis.call('INCR',KEYS[3]); return 1
        end
        return 0
    """, 3, marker, 'ratelimit:worker:by_decision', 'ratelimit:worker:processed',
        event.get('decision', 'unknown'))
    return inserted
