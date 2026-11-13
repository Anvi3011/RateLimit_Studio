import os
import logging
from datetime import datetime, timezone
import redis
from db import RequestLog, get_session, parse_dt

logger = logging.getLogger(__name__)

def save_to_db(event, db_session=None):
    session = db_session
    close_session = False
    if session is None:
        try:
            session = get_session()
            close_session = True
        except Exception as e:
            logger.warning("Could not obtain DB session in worker: %s", e)
            return False

    try:
        req_id = str(event.get('request_id') or event.get('event_id'))
        existing = session.query(RequestLog).filter_by(request_id=req_id).first()
        if existing:
            return True  # Idempotent duplicate check

        req_ts = parse_dt(event.get('request_timestamp') or event.get('timestamp')) or datetime.now(timezone.utc)
        resp_ts = parse_dt(event.get('response_timestamp'))

        decision_raw = event.get('decision', 'unknown')
        if str(decision_raw).lower() in ('accepted', 'allowed'):
            decision = 'ALLOWED'
        elif str(decision_raw).lower() in ('error', '503'):
            decision = 'ERROR'
        else:
            decision = 'REJECTED'

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
            response_status=int(event.get('status', 200)),
            rate_limit_decision=decision,
            rejection_reason=event.get('reason'),
            request_processing_time_ms=float(event.get('latency_ms', 0.0)),
            configured_rate_limit=event.get('configured_rate_limit'),
            burst_capacity=event.get('burst_capacity'),
            window_duration=event.get('window_duration'),
            edge_node_id=event.get('edge_id') or event.get('edge_node_id')
        )
        session.add(log_entry)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error("Failed to persist request log to PostgreSQL: %s", e)
        raise
    finally:
        if close_session:
            session.close()

def process(event, r=None, db_session=None):
    r = r or redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    marker = 'ratelimit:event:processed:' + str(event['event_id'])

    # Check Redis deduplication marker
    is_new = r.set(marker, '1', nx=True, ex=604800)
    if not is_new:
        return False

    # Write to persistent PostgreSQL storage
    try:
        save_to_db(event, db_session=db_session)
    except Exception as exc:
        # Remove marker so failed event can be retried by RabbitMQ
        try:
            r.delete(marker)
        except Exception:
            pass
        raise exc

    r.hincrby('ratelimit:worker:by_decision', event.get('decision', 'unknown'), 1)
    r.incr('ratelimit:worker:processed')
    return True
