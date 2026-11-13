import csv
import io
import json
import logging
import os
import time
import uuid
from flask import Blueprint, current_app, jsonify, request, g, Response
from ..redis import client, decide
from ..session import get_current_session_id
from ..db import (
    log_configuration_change,
    log_fault_injection,
    log_fault_recovery,
    log_system_event,
    get_logs_summary,
    get_request_logs,
    get_request_log_by_id,
    get_configuration_logs,
    get_fault_logs,
    get_system_events,
    cleanup_expired_sessions
)

api = Blueprint('api', __name__)
log = logging.getLogger(__name__)

DEFAULT = {
    'algorithm': 'token_bucket',
    'rate': 5,
    'burst': 10,
    'window_seconds': 1,
    'edge_nodes': 3,
    'identity_strategy': 'header',
    'policy_id': 'default',
    'endpoint': '/api/demo'
}

WORKER_KEY = 'ratelimit:worker:last_seen'
OUTBOX_KEY = 'ratelimit:outbox'
OUTBOX_CAPACITY = max(1, min(100000, int(os.getenv('OUTBOX_CAPACITY', '5000'))))

def store():
    return client()

def get_session_id():
    return get_current_session_id()

def config_value(session_id=None):
    sid = session_id or get_session_id()
    try:
        raw = store().get(f'ratelimit:config:{sid}')
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    cfg = DEFAULT.copy()
    cfg['policy_id'] = sid
    return cfg

def active_faults(session_id=None):
    sid = session_id or get_session_id()
    active = {}
    fault_names = ('redis_unavailable', 'rabbitmq_unavailable', 'worker_unavailable', 'api_degraded', 'network_timeout')
    try:
        r = store()
        for name in fault_names:
            key = f'ratelimit:fault:{sid}:{name}'
            if r.exists(key):
                if r.ttl(key) == -1:
                    r.expire(key, 60)
                active[name] = True

        raw = r.get(f'ratelimit:fault:{sid}:latency_ms')
        if raw is not None:
            active['latency_ms'] = int(raw)
    except Exception:
        pass
    return active

def enqueue_event(event):
    payload = json.dumps(event, separators=(',', ':'))
    return bool(store().eval(
        "local n=redis.call('LLEN',KEYS[1]); if n>=tonumber(ARGV[1]) then return 0 end; redis.call('RPUSH',KEYS[1],ARGV[2]); return n+1",
        1, OUTBOX_KEY, OUTBOX_CAPACITY, payload
    ))

def validate_config(d, session_id=None):
    if not isinstance(d, dict):
        raise ValueError('JSON object required')
    d_clean = {k: v for k, v in d.items() if k not in ('admin_token',)}
    sid = session_id or get_session_id()
    current = config_value(sid)
    c = {**current, **d_clean}
    c['policy_id'] = sid
    if c.get('algorithm') not in ('token_bucket', 'sliding_window'):
        raise ValueError('algorithm must be token_bucket or sliding_window')
    for k, lo, hi in [('rate', 1, 100000), ('burst', 1, 100000), ('window_seconds', 1, 3600), ('edge_nodes', 1, 100)]:
        try:
            c[k] = int(c[k])
        except (ValueError, TypeError):
            raise ValueError(f'{k} must be an integer')
        if not lo <= c[k] <= hi:
            raise ValueError(f'{k} must be between {lo} and {hi}')
    if c.get('identity_strategy') not in ('header', 'ip'):
        raise ValueError('identity_strategy must be header or ip')
    if not isinstance(c.get('endpoint', '/api/demo'), str) or len(c.get('endpoint', '/api/demo')) > 256:
        raise ValueError('endpoint must be a string of at most 256 characters')
    return c

# =====================================================================
# SESSION ENDPOINT
# =====================================================================
@api.get('/session')
def session_info():
    sid = get_session_id()
    is_new = getattr(g, 'is_new_session', False)
    return jsonify({
        'session_id': sid,
        'is_new': is_new,
        'status': 'active',
        'message': 'Demo session active and isolated'
    }), 200

# =====================================================================
# HEALTH CHECK
# =====================================================================
@api.get('/health')
def health():
    result = {'api': 'healthy', 'redis': 'unavailable', 'rabbitmq': 'unknown', 'worker': 'unknown', 'publisher': 'unknown', 'database': 'unknown'}
    try:
        store().ping()
        result['redis'] = 'healthy'
    except Exception:
        pass
    try:
        import pika
        p = pika.URLParameters(os.getenv('RABBITMQ_URL', 'amqp://localhost:5672/%2F'))
        p.socket_timeout = 0.5
        c = pika.BlockingConnection(p)
        c.close()
        result['rabbitmq'] = 'healthy'
    except Exception:
        result['rabbitmq'] = 'unavailable'
    try:
        age = store().ttl(WORKER_KEY)
        result['worker'] = 'healthy' if age > 0 else 'stale'
        result['publisher'] = 'healthy' if store().ttl('ratelimit:publisher:last_seen') > 0 else 'stale'
    except Exception:
        pass
    try:
        from ..db import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        result['database'] = 'healthy'
    except Exception:
        result['database'] = 'unavailable'
    result['healthy'] = all(result[name] == 'healthy' for name in ('api', 'redis', 'rabbitmq', 'worker', 'publisher'))
    return jsonify(result), (200 if result['healthy'] else 503)

# =====================================================================
# CONFIGURATION ENDPOINTS (PUBLIC, SESSION-ISOLATED)
# =====================================================================
@api.get('/config')
def get_config():
    sid = get_session_id()
    try:
        return jsonify(config_value(sid))
    except Exception as e:
        return jsonify(error='redis_unavailable', detail=str(e)), 503

@api.put('/config')
def put_config():
    sid = get_session_id()
    try:
        prev = config_value(sid)
        body = request.get_json(silent=True) or {}
        c = validate_config(body, session_id=sid)

        # Persist in Redis
        store().set(f'ratelimit:config:{sid}', json.dumps(c), ex=86400)

        # Log to PostgreSQL
        try:
            log_configuration_change(
                session_id=sid,
                prev_alg=prev.get('algorithm'),
                new_alg=c.get('algorithm'),
                prev_cfg=prev,
                new_cfg=c,
                status='APPLIED'
            )
        except Exception as exc:
            log.warning("Could not persist configuration change log: %s", exc)

        return jsonify(c)
    except ValueError as e:
        return jsonify(error='invalid_config', detail=str(e)), 400
    except Exception as exc:
        log.error("Failed to update config: %s", exc)
        return jsonify(error='redis_unavailable'), 503

# =====================================================================
# REQUEST TEST & TRAFFIC GENERATION
# =====================================================================
@api.post('/requests/test')
def test_request():
    sid = get_session_id()
    d = request.get_json(silent=True) or {}
    c = config_value(sid)
    client_id = str((request.remote_addr if c.get('identity_strategy') == 'ip' else None) or d.get('client_id') or request.headers.get('X-Client-ID') or request.remote_addr or 'anonymous')[:128]
    endpoint = str(d.get('endpoint') or '/api/demo')[:256]
    edge = str(d.get('edge_id') or ('edge-' + str(int(time.time() * 1000) % 3)))[:64]
    started = time.perf_counter()
    started_ts = time.time()
    rid = str(uuid.uuid4())
    correlation_id = request.headers.get('X-Correlation-ID') or str(uuid.uuid4())
    result = None
    error = None

    faults = active_faults(sid)
    is_internal = getattr(g, 'internal_traffic', False)
    if faults.get('network_timeout') and not is_internal:
        time.sleep(6)
    if faults.get('api_degraded'):
        time.sleep(min(2, faults.get('latency_ms', 250) / 1000))
    elif faults.get('latency_ms'):
        time.sleep(min(2, faults['latency_ms'] / 1000))

    try:
        if faults.get('redis_unavailable'):
            raise ConnectionError('Redis fault injected')
        result = decide(client_id, endpoint, c, edge)
    except Exception as e:
        error = str(e)
        allowed = (current_app.config['FAIL_MODE'] == 'open')
        result = {'allowed': allowed, 'remaining': 0, 'retry_after': 1, 'redis_latency_ms': 0}

    latency = (time.perf_counter() - started) * 1000
    finished_ts = time.time()
    failed_closed = bool(error and not result['allowed'])
    status = 503 if failed_closed else (200 if result['allowed'] else 429)
    decision = 'error' if failed_closed else ('accepted' if result['allowed'] else 'rejected')
    decision_formal = 'ALLOWED' if result['allowed'] else ('ERROR' if failed_closed else 'REJECTED')
    rejection_reason = error or (None if result['allowed'] else 'Rate limit exceeded')

    record = {
        'event_id': rid,
        'request_id': rid,
        'correlation_id': correlation_id,
        'demo_session_id': sid,
        'client_id': client_id,
        'algorithm': c['algorithm'],
        'endpoint': endpoint,
        'http_method': 'POST',
        'request_timestamp': started_ts,
        'response_timestamp': finished_ts,
        'timestamp': started_ts,
        'status': status,
        'decision': decision,
        'rate_limit_decision': decision_formal,
        'reason': rejection_reason,
        'latency_ms': round(latency, 3),
        'remaining': result['remaining'],
        'retry_after': result['retry_after'],
        'configured_rate_limit': c['rate'],
        'burst_capacity': c['burst'],
        'window_duration': c['window_seconds'],
        'edge_id': edge,
        'edge_node_id': edge,
        'policy_id': c['policy_id']
    }

    if faults.get('rabbitmq_unavailable'):
        record['event_publish_error'] = 'RabbitMQ fault injected'

    # Update session metrics in Redis (strictly isolated per session)
    try:
        r = store()
        # Session metrics
        mkey = f'ratelimit:metrics:{sid}'
        r.hincrby(mkey, 'total', 1)
        r.hincrby(mkey, decision, 1)
        r.expire(mkey, 86400)

        lkey = f'ratelimit:latencies:{sid}'
        r.lpush(lkey, latency)
        r.ltrim(lkey, 0, 4999)
        r.expire(lkey, 86400)

        tkey = f'ratelimit:request_times:{sid}'
        r.lpush(tkey, started_ts)
        r.ltrim(tkey, 0, 4999)
        r.expire(tkey, 86400)

        hkey = f'ratelimit:history:{sid}'
        r.lpush(hkey, json.dumps(record))
        r.ltrim(hkey, 0, 4999)
        r.expire(hkey, 86400)

        ckey = f'ratelimit:chart_samples:{sid}'
        sample_member = json.dumps({
            'request_id': rid,
            'timestamp': started_ts,
            'accepted': int(decision == 'accepted'),
            'rejected': int(decision == 'rejected'),
            'errors': int(decision == 'error'),
            'latency_ms': round(latency, 3)
        })
        r.zadd(ckey, {sample_member: started_ts})
        r.zremrangebyscore(ckey, '-inf', started_ts - 3600)
        r.expire(ckey, 86400)

        # Global aggregate counter for system-level stats (no visitor records stored globally)
        r.hincrby('ratelimit:metrics', 'total', 1)
        r.hincrby('ratelimit:metrics', decision, 1)
    except Exception:
        pass

    # Asynchronous persistence through RabbitMQ Outbox
    if not faults.get('rabbitmq_unavailable'):
        try:
            if not enqueue_event(record):
                record['event_publish_error'] = 'publisher_outbox_full'
                store().hincrby(f'ratelimit:metrics:{sid}', 'outbox_overflow', 1)
                store().hincrby(f'ratelimit:metrics:{sid}', 'publish_failures', 1)
                store().hincrby('ratelimit:metrics', 'outbox_overflow', 1)
                store().hincrby('ratelimit:metrics', 'publish_failures', 1)
        except Exception as exc:
            log.warning('outbox enqueue failed request_id=%s error=%s', rid, type(exc).__name__)
            record['event_publish_error'] = 'outbox_unavailable'
    else:
        try:
            store().hincrby(f'ratelimit:metrics:{sid}', 'publish_failures', 1)
            store().hincrby('ratelimit:metrics', 'publish_failures', 1)
        except Exception:
            pass

    response = jsonify(record)
    response.status_code = status
    response.headers['X-RateLimit-Remaining'] = str(record['remaining'])
    response.headers['Retry-After'] = str(record['retry_after'])
    return response

@api.post('/traffic/generate')
def traffic():
    sid = get_session_id()
    d = request.get_json(silent=True) or {}
    try:
        count = int(d.get('count', 10))
        rate = float(d.get('requests_per_second', 100))
    except (TypeError, ValueError):
        return jsonify(error='invalid_traffic_parameters'), 400
    if not 1 <= count <= 500 or not 0 < rate <= 200:
        return jsonify(error='count must be 1..500 and requests_per_second 0..200'), 400

    log_system_event(
        event_type='TRAFFIC_GENERATION_STARTED',
        component='TrafficGenerator',
        severity='INFO',
        message=f'Starting synthetic traffic batch of {count} requests at {rate} rps',
        session_id=sid,
        metadata={'count': count, 'rate': rate, 'client_id': d.get('client_id', 'demo-client')}
    )

    gap = 1.0 / rate
    records = []
    start = time.monotonic()
    client_id = d.get('client_id', 'demo-client')
    endpoint = d.get('endpoint', '/api/demo')

    for i in range(count):
        with current_app.test_request_context(
            '/api/requests/test',
            method='POST',
            json={'client_id': client_id, 'endpoint': endpoint}
        ):
            g.session_id = sid
            g.internal_traffic = True
            resp = test_request()
            records.append(resp.get_json())

        delay = start + (i + 1) * gap - time.monotonic()
        if delay > 0 and i + 1 < count:
            time.sleep(min(delay, 2))

    accepted = sum(x['decision'] == 'accepted' for x in records)
    rejected = sum(x['decision'] == 'rejected' for x in records)
    errors = sum(x['decision'] == 'error' for x in records)

    log_system_event(
        event_type='TRAFFIC_GENERATION_COMPLETED',
        component='TrafficGenerator',
        severity='INFO',
        message=f'Completed synthetic traffic batch: {accepted} accepted, {rejected} rejected, {errors} errors',
        session_id=sid,
        metadata={'offered': count, 'accepted': accepted, 'rejected': rejected, 'errors': errors}
    )

    return jsonify(
        offered=count,
        accepted=accepted,
        rejected=rejected,
        errors=errors,
        records=records
    )

# =====================================================================
# HISTORY & METRICS
# =====================================================================
@api.get('/history')
def history():
    sid = get_session_id()
    try:
        r = store()
        hkey = f'ratelimit:history:{sid}'
        raw_items = r.lrange(hkey, 0, 4999)
        rows = [json.loads(x) for x in raw_items]

        decision = request.args.get('decision')
        client_id = request.args.get('client_id', '').lower()
        endpoint = request.args.get('endpoint', '').lower()
        if decision in ('accepted', 'rejected'):
            rows = [x for x in rows if x['decision'] == decision]
        if client_id:
            rows = [x for x in rows if client_id in x['client_id'].lower()]
        if endpoint:
            rows = [x for x in rows if endpoint in x['endpoint'].lower()]
        if request.args.get('since'):
            try:
                since = float(request.args['since'])
                rows = [x for x in rows if x['timestamp'] >= since]
            except ValueError:
                return jsonify(error='since must be a Unix timestamp'), 400
        try:
            page = max(1, int(request.args.get('page', 1)))
            size = min(100, max(1, int(request.args.get('page_size', 50))))
        except ValueError:
            return jsonify(error='invalid_pagination'), 400
        return jsonify(items=rows[(page - 1) * size:page * size], page=page, page_size=size, total=len(rows))
    except Exception:
        return jsonify(error='redis_unavailable'), 503

@api.get('/metrics')
def metrics():
    sid = get_session_id()
    try:
        r = store()
        mkey = f'ratelimit:metrics:{sid}'
        m = {k: int(v) for k, v in r.hgetall(mkey).items()}

        lat = [float(v) for v in r.lrange(f'ratelimit:latencies:{sid}', 0, 4999)]
        lat.sort()

        pct = lambda p: round(lat[min(len(lat) - 1, int((len(lat) - 1) * p))], 2) if lat else 0
        total = m.get('total', 0)
        accepted = m.get('accepted', 0)
        rejected = m.get('rejected', 0)
        errors = m.get('error', 0)
        decisions = accepted + rejected
        depth = 0
        try:
            import pika
            p = pika.URLParameters(os.getenv('RABBITMQ_URL', 'amqp://localhost:5672/%2F'))
            p.socket_timeout = 0.5
            c = pika.BlockingConnection(p)
            depth = c.channel().queue_declare('ratelimit.events', durable=True, passive=True).method.message_count
            c.close()
        except Exception:
            pass

        now = time.time()
        times = [float(v) for v in r.lrange(f'ratelimit:request_times:{sid}', 0, 4999)]
        recent = sum(1 for ts in times if ts >= now - 10)
        accepted_recent = 0
        for raw in r.lrange(f'ratelimit:history:{sid}', 0, 4999):
            row = json.loads(raw)
            if row['timestamp'] >= now - 10 and row['decision'] == 'accepted':
                accepted_recent += 1

        return jsonify(
            total=total,
            accepted=accepted,
            rejected=rejected,
            errors=errors,
            acceptance_rate=round(accepted / decisions * 100, 2) if decisions else 0,
            rejection_rate=round(rejected / decisions * 100, 2) if decisions else 0,
            error_rate=round(errors / total * 100, 2) if total else 0,
            p50_ms=pct(0.5),
            p95_ms=pct(0.95),
            p99_ms=pct(0.99),
            throughput_rps=round(recent / 10, 2),
            goodput_rps=round(accepted_recent / 10, 2),
            queue_depth=depth,
            worker_processed=int(r.get('ratelimit:worker:processed') or 0),
            worker_failures=int(r.get('ratelimit:worker:failures') or 0),
            publish_failures=m.get('publish_failures', 0),
            events_published=m.get('events_published', 0),
            publisher_retries=m.get('publisher_retries', 0),
            outbox_depth=r.llen(OUTBOX_KEY),
            outbox_inflight=r.llen(OUTBOX_KEY + ':inflight'),
            outbox_failed=r.llen(OUTBOX_KEY + ':failed'),
            outbox_overflow=m.get('outbox_overflow', 0),
            api_utilization='unavailable'
        )
    except Exception:
        return jsonify(error='redis_unavailable'), 503

@api.get('/metrics/timeseries')
def metrics_timeseries():
    sid = get_session_id()
    try:
        seconds = max(60, min(3600, int(request.args.get('seconds', 300))))
        bucket = max(1, min(60, int(request.args.get('bucket_seconds', 5))))
        now = int(time.time())
        start = now - seconds

        ckey = f'ratelimit:chart_samples:{sid}'
        samples = store().zrangebyscore(ckey, start, '+inf')
        groups = {}
        for raw in samples:
            row = json.loads(raw)
            slot = int(row['timestamp'] // bucket * bucket)
            item = groups.setdefault(slot, {'timestamp': slot, 'accepted': 0, 'rejected': 0, 'errors': 0, 'latency_total_ms': 0.0, 'requests': 0})
            for key in ('accepted', 'rejected', 'errors'):
                item[key] += int(row[key])
            item['latency_total_ms'] += float(row['latency_ms'])
            item['requests'] += 1
        points = []
        for slot in range(start // bucket * bucket, now + 1, bucket):
            p = groups.get(slot, {'timestamp': slot, 'accepted': 0, 'rejected': 0, 'errors': 0, 'latency_total_ms': 0.0, 'requests': 0})
            p['avg_latency_ms'] = round(p.pop('latency_total_ms') / p['requests'], 2) if p['requests'] else 0
            points.append(p)
        return jsonify(source='backend Redis decision samples', seconds=seconds, bucket_seconds=bucket, points=points)
    except (ValueError, TypeError):
        return jsonify(error='invalid time-series parameters'), 400
    except Exception:
        return jsonify(error='redis_unavailable'), 503

@api.delete('/metrics')
def reset_metrics():
    sid = get_session_id()
    try:
        r = store()
        r.delete(
            f'ratelimit:metrics:{sid}',
            f'ratelimit:latencies:{sid}',
            f'ratelimit:request_times:{sid}',
            f'ratelimit:history:{sid}',
            f'ratelimit:chart_samples:{sid}'
        )
        return jsonify(reset=True)
    except Exception:
        return jsonify(error='redis_unavailable'), 503

@api.get('/queue/status')
def queue_status():
    return jsonify(depth=metrics().get_json().get('queue_depth', 0))

@api.get('/worker/status')
def worker_status():
    try:
        sid = get_session_id()
        faults = active_faults(sid)

        r = store()
        processed = int(r.get('ratelimit:worker:processed') or 0)

        if faults.get('worker_unavailable'):
            return jsonify(status='stale', processed=processed)

        status = 'healthy' if r.ttl(WORKER_KEY) > 0 else 'stale'
        return jsonify(status=status, processed=processed)

    except Exception:
        return jsonify(status='unavailable'), 503
# =====================================================================
# FAULT SIMULATION (PUBLIC, CONTROLLED, SESSION-ISOLATED)
# =====================================================================
@api.get('/faults')
def get_faults():
    sid = get_session_id()
    active = active_faults(sid)
    ttl = {}
    try:
        r = store()
        for name in active:
            ttl[name] = max(0, r.ttl(f'ratelimit:fault:{sid}:{name}'))
    except Exception:
        pass
    return jsonify(enabled=current_app.config['FAULTS_ENABLED'], active=active, ttl_seconds=ttl)

@api.post('/faults')
def set_faults():
    if not current_app.config['FAULTS_ENABLED']:
        return jsonify(error='fault_injection_disabled'), 403
    sid = get_session_id()
    d = request.get_json(silent=True) or {}
    name = d.get('name')
    enabled = bool(d.get('enabled', True))
    if name not in ('redis_unavailable', 'rabbitmq_unavailable', 'worker_unavailable', 'api_degraded', 'network_timeout', 'latency_ms'):
        return jsonify(error='unsupported_fault'), 400
    try:
        duration = max(1, min(300, int(d.get('duration_seconds', 60))))
    except (ValueError, TypeError):
        return jsonify(error='duration_seconds must be between 1 and 300'), 400

    component_map = {
        'redis_unavailable': 'Redis State Store',
        'rabbitmq_unavailable': 'RabbitMQ Message Broker',
        'worker_unavailable': 'Worker Consumer',
        'api_degraded': 'Flask API',
        'network_timeout': 'Edge Reverse Proxy',
        'latency_ms': 'Flask API'
    }

    if name == 'latency_ms':
        if not enabled:
            store().delete(f'ratelimit:fault:{sid}:latency_ms')
            log_fault_recovery(sid, 'latency_ms')
            return get_faults()
        try:
            value = max(0, min(2000, int(d.get('value', 250))))
        except (ValueError, TypeError):
            return jsonify(error='latency_ms must be an integer'), 400
        store().set(f'ratelimit:fault:{sid}:latency_ms', str(value), ex=duration)
        log_fault_injection(sid, 'latency_ms', component_map['latency_ms'], duration, f'Added {value}ms artificial delay')
    else:
        try:
            fkey = f'ratelimit:fault:{sid}:{name}'
            if enabled:
                store().set(fkey, '1', ex=duration)
                # Keep compatibility key
                log_fault_injection(sid, name, component_map.get(name, 'System'), duration)
            else:
                store().delete(fkey)
                log_fault_recovery(sid, name)
        except Exception:
            return jsonify(error='redis_unavailable'), 503
    return get_faults()

@api.delete('/faults')
def reset_faults():
    sid = get_session_id()
    try:
        r = store()
        for name in ('redis_unavailable', 'rabbitmq_unavailable', 'worker_unavailable', 'api_degraded', 'network_timeout', 'latency_ms'):
            r.delete(f'ratelimit:fault:{sid}:{name}')
        log_fault_recovery(sid)
    except Exception:
        return jsonify(error='redis_unavailable'), 503
    return jsonify(active={})

# =====================================================================
# BENCHMARKS (PUBLIC, SESSION-ISOLATED)
# =====================================================================
@api.post('/benchmarks/run')
def benchmark():
    sid = get_session_id()
    d = request.get_json(silent=True) or {}
    alg = d.get('algorithm', 'token_bucket')
    try:
        count = min(500, max(1, int(d.get('count', 100))))
    except (TypeError, ValueError):
        return jsonify(error='count must be an integer from 1 to 500'), 400

    old = config_value(sid)
    c = validate_config({'algorithm': alg}, session_id=sid)
    store().set(f'ratelimit:config:{sid}', json.dumps(c), ex=86400)
    t = time.perf_counter()
    accepted = 0
    lat = []

    for i in range(count):
        with current_app.test_request_context(
            '/api/requests/test',
            method='POST',
            json={'client_id': f'bench-{i%10}'}
        ):
            g.session_id = sid
            g.internal_traffic = True
            before = time.perf_counter()
            resp = test_request()
            lat.append((time.perf_counter() - before) * 1000)
            accepted += (resp.status_code == 200)

    elapsed = time.perf_counter() - t
    store().set(f'ratelimit:config:{sid}', json.dumps(old), ex=86400)
    lat.sort()

    report = {
        'id': str(uuid.uuid4()),
        'algorithm': alg,
        'offered': count,
        'duration_seconds': round(elapsed, 3),
        'offered_rps': round(count / elapsed, 2),
        'accepted': accepted,
        'rejected': count - accepted,
        'p50_ms': lat[len(lat) // 2] if lat else 0,
        'p95_ms': lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0,
        'p99_ms': lat[min(len(lat) - 1, int(len(lat) * 0.99))] if lat else 0
    }
    try:
        store().lpush(f'ratelimit:benchmarks:{sid}', json.dumps(report))
        store().expire(f'ratelimit:benchmarks:{sid}', 86400)
    except Exception:
        pass
    return jsonify(report), 201

@api.get('/benchmarks')
def benchmarks():
    sid = get_session_id()
    try:
        raw = store().lrange(f'ratelimit:benchmarks:{sid}', 0, 99)
        return jsonify(items=[json.loads(x) for x in raw])
    except Exception:
        return jsonify(error='redis_unavailable'), 503

# =====================================================================
# LOGS & ANALYTICS API (SESSION-ISOLATED & PERSISTENT)
# =====================================================================
@api.get('/logs/summary')
def logs_summary():
    sid = get_session_id()
    try:
        summary = get_logs_summary(sid)
        return jsonify(summary), 200
    except Exception as e:
        log.error("Failed to fetch logs summary: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.get('/logs/requests')
def logs_requests():
    sid = get_session_id()
    filters = {
        'algorithm': request.args.get('algorithm'),
        'decision': request.args.get('decision'),
        'client_id': request.args.get('client_id'),
        'endpoint': request.args.get('endpoint'),
        'request_id': request.args.get('request_id'),
        'status': request.args.get('status'),
        'from_timestamp': request.args.get('from_timestamp'),
        'to_timestamp': request.args.get('to_timestamp')
    }
    # Clean filters
    filters = {k: v for k, v in filters.items() if v and v.lower() != 'all'}
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 50)
    try:
        data = get_request_logs(sid, filters, page, page_size)
        return jsonify(data), 200
    except Exception as e:
        log.error("Failed to fetch request logs: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.get('/logs/requests/<request_id>')
def logs_request_detail(request_id):
    sid = get_session_id()
    try:
        record = get_request_log_by_id(sid, request_id)
        if not record:
            return jsonify(error='not_found', detail='Request log record not found for this session'), 404
        return jsonify(record), 200
    except Exception as e:
        log.error("Failed to fetch request log detail: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.get('/logs/configurations')
def logs_configurations():
    sid = get_session_id()
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 50)
    try:
        data = get_configuration_logs(sid, page, page_size)
        return jsonify(data), 200
    except Exception as e:
        log.error("Failed to fetch configuration logs: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.get('/logs/faults')
def logs_faults():
    sid = get_session_id()
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 50)
    try:
        data = get_fault_logs(sid, page, page_size)
        return jsonify(data), 200
    except Exception as e:
        log.error("Failed to fetch fault logs: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.get('/logs/system-events')
def logs_system_events():
    sid = get_session_id()
    severity = request.args.get('severity')
    component = request.args.get('component')
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 50)
    try:
        data = get_system_events(sid, severity, component, page, page_size)
        return jsonify(data), 200
    except Exception as e:
        log.error("Failed to fetch system events: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.get('/logs/requests/export.csv')
def export_request_logs_csv():
    sid = get_session_id()
    try:
        # Fetch up to 1000 latest logs for export
        data = get_request_logs(sid, filters={}, page=1, page_size=1000)
        items = data.get('items', [])

        def sanitize_csv_cell(val):
            """Prevent spreadsheet formula injection."""
            if val is None:
                return ''
            s = str(val)
            if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
                return "'" + s
            return s

        output = io.StringIO()
        fieldnames = [
            'timestamp', 'request_id', 'correlation_id', 'client_id',
            'algorithm', 'endpoint', 'http_method', 'decision',
            'response_status', 'processing_time_ms', 'rejection_reason',
            'configured_rate', 'burst_capacity', 'window_duration', 'edge_node_id'
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for item in items:
            writer.writerow({
                'timestamp': sanitize_csv_cell(item.get('request_timestamp')),
                'request_id': sanitize_csv_cell(item.get('request_id')),
                'correlation_id': sanitize_csv_cell(item.get('correlation_id')),
                'client_id': sanitize_csv_cell(item.get('client_id')),
                'algorithm': sanitize_csv_cell(item.get('algorithm')),
                'endpoint': sanitize_csv_cell(item.get('endpoint')),
                'http_method': sanitize_csv_cell(item.get('http_method')),
                'decision': sanitize_csv_cell(item.get('rate_limit_decision')),
                'response_status': sanitize_csv_cell(item.get('response_status')),
                'processing_time_ms': sanitize_csv_cell(item.get('request_processing_time_ms')),
                'rejection_reason': sanitize_csv_cell(item.get('rejection_reason')),
                'configured_rate': sanitize_csv_cell(item.get('configured_rate_limit')),
                'burst_capacity': sanitize_csv_cell(item.get('burst_capacity')),
                'window_duration': sanitize_csv_cell(item.get('window_duration')),
                'edge_node_id': sanitize_csv_cell(item.get('edge_node_id'))
            })

        csv_bytes = output.getvalue()
        return Response(
            csv_bytes,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=request_logs_{sid[:12]}.csv',
                'Cache-Control': 'no-cache'
            }
        )
    except Exception as e:
        log.error("Failed to export CSV: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503

@api.post('/logs/cleanup')
def logs_cleanup():
    retention_hours = int(current_app.config.get('LOG_RETENTION_HOURS', 24))
    try:
        res = cleanup_expired_sessions(retention_hours=retention_hours)
        return jsonify(res), 200
    except Exception as e:
        log.error("Failed to run logs cleanup: %s", e)
        return jsonify(error='database_unavailable', detail=str(e)), 503
