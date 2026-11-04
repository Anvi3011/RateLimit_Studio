import json, os, time, uuid, hmac, hashlib
import logging
from flask import Blueprint, current_app, jsonify, request
from ..redis import client, decide

api=Blueprint('api',__name__)
DEFAULT={'algorithm':'token_bucket','rate':5,'burst':10,'window_seconds':1,'edge_nodes':3,'identity_strategy':'header','policy_id':'default','endpoint':'/api/demo'}
FAULTS={}
log=logging.getLogger(__name__)
WORKER_KEY='ratelimit:worker:last_seen'
POLICY_CACHE=DEFAULT.copy()
OUTBOX_KEY='ratelimit:outbox'
OUTBOX_CAPACITY=max(1,min(100000,int(os.getenv('OUTBOX_CAPACITY','5000'))))

def store(): return client()
def config_value():
    global POLICY_CACHE
    try: raw=store().get('ratelimit:config')
    except Exception: return POLICY_CACHE.copy()
    POLICY_CACHE=json.loads(raw) if raw else DEFAULT.copy()
    return POLICY_CACHE.copy()
def admin():
    expected=current_app.config['ADMIN_TOKEN']; supplied=request.headers.get('X-Admin-Token','')
    if not expected or expected=='change-me-before-use': return False
    try:
        key='ratelimit:admin:attempts:'+hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest(); r=store(); count=int(r.get(key) or 0)
        if count>=5: return False
        if hmac.compare_digest(supplied,expected): r.delete(key); return True
        p=r.pipeline(); p.incr(key); p.expire(key,60); p.execute()
    except Exception: return False
    return False
def auth_failure():
    try:
        key='ratelimit:admin:attempts:'+hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()
        if int(store().get(key) or 0)>=5: return jsonify(error='admin_auth_throttled',retry_after=60),429,{'Retry-After':'60'}
    except Exception: pass
    return jsonify(error='admin_auth_required'),401
def active_faults():
    active={}
    try:
        for name in ('redis_unavailable','rabbitmq_unavailable','worker_unavailable','api_degraded','network_timeout'):
            key='ratelimit:fault:'+name
            if store().exists(key):
                if store().ttl(key)==-1: store().expire(key,60)  # recover legacy non-expiring worker-pause keys
                active[name]=True
        raw=store().get('ratelimit:fault:latency_ms')
        if raw is not None:
            if store().ttl('ratelimit:fault:latency_ms')==-1: store().expire('ratelimit:fault:latency_ms',60)
            active['latency_ms']=int(raw)
    except Exception: pass
    active.update(FAULTS); return active
def enqueue_event(event):
    payload=json.dumps(event,separators=(',',':'))
    return bool(store().eval("local n=redis.call('LLEN',KEYS[1]); if n>=tonumber(ARGV[1]) then return 0 end; redis.call('RPUSH',KEYS[1],ARGV[2]); return n+1",1,OUTBOX_KEY,OUTBOX_CAPACITY,payload))
def validate_config(d):
    if not isinstance(d,dict): raise ValueError('JSON object required')
    if 'algorithm' in d and d['algorithm'] not in ('token_bucket','sliding_window'): raise ValueError('algorithm must be token_bucket or sliding_window')
    c={**config_value(),**d}
    if c.get('algorithm') not in ('token_bucket','sliding_window'): raise ValueError('algorithm must be token_bucket or sliding_window')
    for k,lo,hi in [('rate',1,100000),('burst',1,100000),('window_seconds',1,3600),('edge_nodes',1,100)]:
        try: c[k]=int(c[k])
        except (ValueError,TypeError): raise ValueError(f'{k} must be an integer')
        if not lo<=c[k]<=hi: raise ValueError(f'{k} must be between {lo} and {hi}')
    if c.get('identity_strategy') not in ('header','ip'): raise ValueError('identity_strategy must be header or ip')
    if not isinstance(c.get('endpoint','/api/demo'),str) or len(c.get('endpoint','/api/demo'))>256: raise ValueError('endpoint must be a string of at most 256 characters')
    if not isinstance(c.get('policy_id','default'),str) or not c.get('policy_id','default').replace('-','').replace('_','').isalnum(): raise ValueError('policy_id may contain only letters, numbers, hyphen, and underscore')
    return c

@api.get('/health')
def health():
    result={'api':'healthy','redis':'unavailable','rabbitmq':'unknown','worker':'unknown','publisher':'unknown'}
    try: store().ping(); result['redis']='healthy'
    except Exception: pass
    try:
        import pika
        p=pika.URLParameters(os.getenv('RABBITMQ_URL','amqp://localhost:5672/%2F')); p.socket_timeout=.5
        c=pika.BlockingConnection(p); c.close(); result['rabbitmq']='healthy'
    except Exception: result['rabbitmq']='unavailable'
    try:
        age=store().ttl(WORKER_KEY); result['worker']='healthy' if age>0 else 'stale'
        result['publisher']='healthy' if store().ttl('ratelimit:publisher:last_seen')>0 else 'stale'
    except Exception: pass
    result['healthy']=all(result[name]=='healthy' for name in ('api','redis','rabbitmq','worker','publisher'))
    return jsonify(result),200 if result['healthy'] else 503

@api.get('/config')
def get_config():
    try: return jsonify(config_value())
    except Exception as e: return jsonify(error='redis_unavailable',detail=str(e)),503

@api.put('/config')
def put_config():
    if not admin(): return auth_failure()
    try:
        c=validate_config(request.get_json(silent=True)); store().set('ratelimit:config',json.dumps(c))
        global POLICY_CACHE
        POLICY_CACHE=c.copy(); return jsonify(c)
    except ValueError as e: return jsonify(error='invalid_config',detail=str(e)),400
    except Exception: return jsonify(error='redis_unavailable'),503

@api.post('/requests/test')
def test_request():
    d=request.get_json(silent=True) or {}; c=config_value()
    client_id=str((request.remote_addr if c.get('identity_strategy')=='ip' else None) or d.get('client_id') or request.headers.get('X-Client-ID') or request.remote_addr or 'anonymous')[:128]
    endpoint=str(d.get('endpoint') or '/api/demo')[:256]; edge=str(d.get('edge_id') or ('edge-'+str(int(time.time()*1000)%3)))[:64]
    started=time.perf_counter(); rid=str(uuid.uuid4()); result=None; error=None
    faults=active_faults()
    if faults.get('network_timeout') and request.headers.get('X-Internal-Traffic')!='1': time.sleep(6)
    if faults.get('api_degraded'): time.sleep(min(2,faults.get('latency_ms',250)/1000))
    elif faults.get('latency_ms'): time.sleep(min(2,faults['latency_ms']/1000))
    try:
        if faults.get('redis_unavailable'): raise ConnectionError('Redis fault injected')
        result=decide(client_id,endpoint,c,edge)
    except Exception as e:
        error=str(e); allowed=(current_app.config['FAIL_MODE']=='open')
        result={'allowed':allowed,'remaining':0,'retry_after':1,'redis_latency_ms':0}
    latency=(time.perf_counter()-started)*1000
    failed_closed=bool(error and not result['allowed']); status=503 if failed_closed else (200 if result['allowed'] else 429)
    decision='error' if failed_closed else ('accepted' if result['allowed'] else 'rejected')
    record={'request_id':rid,'timestamp':time.time(),'client_id':client_id,'endpoint':endpoint,'edge_id':edge,'algorithm':c['algorithm'],'decision':decision,'status':status,'latency_ms':round(latency,3),'reason':error or (None if result['allowed'] else 'rate_limit_exceeded'),'remaining':result['remaining'],'retry_after':result['retry_after']}
    if faults.get('rabbitmq_unavailable'): record['event_publish_error']='RabbitMQ fault injected'
    try:
        r=store()
        r.hincrby('ratelimit:metrics','total',1); r.hincrby('ratelimit:metrics',decision,1); r.lpush('ratelimit:latencies',latency); r.ltrim('ratelimit:latencies',0,4999)
        r.lpush('ratelimit:request_times',time.time()); r.ltrim('ratelimit:request_times',0,4999)
        stamp=time.time(); r.zadd('ratelimit:chart_samples',{json.dumps({'request_id':rid,'timestamp':stamp,'accepted':int(decision=='accepted'),'rejected':int(decision=='rejected'),'errors':int(decision=='error'),'latency_ms':round(latency,3)}):stamp}); r.zremrangebyscore('ratelimit:chart_samples','-inf',stamp-3600); r.zremrangebyrank('ratelimit:chart_samples',0,-10001)
    except Exception: pass
    if not faults.get('rabbitmq_unavailable'):
        try:
            if not enqueue_event({**record,'event_id':rid,'policy_id':c['policy_id']}):
                record['event_publish_error']='publisher_outbox_full'; store().hincrby('ratelimit:metrics','outbox_overflow',1); store().hincrby('ratelimit:metrics','publish_failures',1)
        except Exception as exc:
            log.warning('outbox enqueue failed request_id=%s error=%s',rid,type(exc).__name__); record['event_publish_error']='outbox_unavailable'
    else:
        record['event_publish_error']='RabbitMQ fault injected'
        try: store().hincrby('ratelimit:metrics','publish_failures',1)
        except Exception: pass
    try:
        store().lpush('ratelimit:history',json.dumps(record)); store().ltrim('ratelimit:history',0,4999)
    except Exception: pass
    response=jsonify(record); response.status_code=status; response.headers['X-RateLimit-Remaining']=str(record['remaining']); response.headers['Retry-After']=str(record['retry_after']); return response

@api.post('/traffic/generate')
def traffic():
    d=request.get_json(silent=True) or {}
    try: count=int(d.get('count',10)); rate=float(d.get('requests_per_second',100))
    except (TypeError,ValueError): return jsonify(error='invalid_traffic_parameters'),400
    if not 1<=count<=500 or not 0<rate<=200: return jsonify(error='count must be 1..500 and requests_per_second 0..200'),400
    gap=1/rate; records=[]; start=time.monotonic()
    for i in range(count):
        with current_app.test_request_context('/api/requests/test',method='POST',json={'client_id':d.get('client_id','demo-client'),'endpoint':d.get('endpoint','/api/demo')},headers={'X-Internal-Traffic':'1'}):
            # invoke the same decision path; this local harness records API results
            resp=test_request(); records.append(resp.get_json())
        delay=start+(i+1)*gap-time.monotonic()
        if delay>0 and i+1<count: time.sleep(min(delay,2))
    return jsonify(offered=count,accepted=sum(x['decision']=='accepted' for x in records),rejected=sum(x['decision']=='rejected' for x in records),errors=sum(x['decision']=='error' for x in records),records=records)

@api.get('/history')
def history():
    try:
        rows=[json.loads(x) for x in store().lrange('ratelimit:history',0,4999)]
        decision=request.args.get('decision'); client_id=request.args.get('client_id','').lower(); endpoint=request.args.get('endpoint','').lower()
        if decision in ('accepted','rejected'): rows=[x for x in rows if x['decision']==decision]
        if client_id: rows=[x for x in rows if client_id in x['client_id'].lower()]
        if endpoint: rows=[x for x in rows if endpoint in x['endpoint'].lower()]
        if request.args.get('since'):
            try: since=float(request.args['since'])
            except ValueError: return jsonify(error='since must be a Unix timestamp'),400
            rows=[x for x in rows if x['timestamp']>=since]
        try: page=max(1,int(request.args.get('page',1))); size=min(100,max(1,int(request.args.get('page_size',50))))
        except ValueError: return jsonify(error='invalid_pagination'),400
        return jsonify(items=rows[(page-1)*size:page*size],page=page,page_size=size,total=len(rows))
    except Exception: return jsonify(error='redis_unavailable'),503

@api.get('/metrics')
def metrics():
    try:
        r=store(); m={k:int(v) for k,v in r.hgetall('ratelimit:metrics').items()}; lat=[float(v) for v in r.lrange('ratelimit:latencies',0,4999)]; lat.sort()
        pct=lambda p: round(lat[min(len(lat)-1,int((len(lat)-1)*p))],2) if lat else 0
        total=m.get('total',0); accepted=m.get('accepted',0); rejected=m.get('rejected',0); errors=m.get('error',0); decisions=accepted+rejected; depth=0
        try:
            import pika
            p=pika.URLParameters(os.getenv('RABBITMQ_URL','amqp://localhost:5672/%2F')); p.socket_timeout=.5; c=pika.BlockingConnection(p); depth=c.channel().queue_declare('ratelimit.events',durable=True,passive=True).method.message_count; c.close()
        except Exception: pass
        now=time.time(); times=[float(v) for v in r.lrange('ratelimit:request_times',0,4999)]; recent=sum(1 for ts in times if ts>=now-10); accepted_recent=0
        for raw in r.lrange('ratelimit:history',0,4999):
            row=json.loads(raw)
            if row['timestamp']>=now-10 and row['decision']=='accepted': accepted_recent+=1
        return jsonify(total=total,accepted=accepted,rejected=rejected,errors=errors,acceptance_rate=round(accepted/decisions*100,2) if decisions else 0,rejection_rate=round(rejected/decisions*100,2) if decisions else 0,error_rate=round(errors/total*100,2) if total else 0,p50_ms=pct(.5),p95_ms=pct(.95),p99_ms=pct(.99),throughput_rps=round(recent/10,2),goodput_rps=round(accepted_recent/10,2),queue_depth=depth,worker_processed=int(r.get('ratelimit:worker:processed') or 0),worker_failures=int(r.get('ratelimit:worker:failures') or 0),publish_failures=m.get('publish_failures',0),events_published=m.get('events_published',0),publisher_retries=m.get('publisher_retries',0),outbox_depth=r.llen(OUTBOX_KEY),outbox_inflight=r.llen(OUTBOX_KEY+':inflight'),outbox_failed=r.llen(OUTBOX_KEY+':failed'),outbox_overflow=m.get('outbox_overflow',0),api_utilization='unavailable')
    except Exception: return jsonify(error='redis_unavailable'),503

@api.get('/metrics/timeseries')
def metrics_timeseries():
    try:
        seconds=max(60,min(3600,int(request.args.get('seconds',300)))); bucket=max(1,min(60,int(request.args.get('bucket_seconds',5))))
        now=int(time.time()); start=now-seconds; samples=store().zrangebyscore('ratelimit:chart_samples',start,'+inf'); groups={}
        for raw in samples:
            row=json.loads(raw); slot=int(row['timestamp']//bucket*bucket); item=groups.setdefault(slot,{'timestamp':slot,'accepted':0,'rejected':0,'errors':0,'latency_total_ms':0.0,'requests':0})
            for key in ('accepted','rejected','errors'): item[key]+=int(row[key])
            item['latency_total_ms']+=float(row['latency_ms']); item['requests']+=1
        points=[]
        for slot in range(start//bucket*bucket,now+1,bucket):
            p=groups.get(slot,{'timestamp':slot,'accepted':0,'rejected':0,'errors':0,'latency_total_ms':0.0,'requests':0})
            p['avg_latency_ms']=round(p.pop('latency_total_ms')/p['requests'],2) if p['requests'] else 0; points.append(p)
        return jsonify(source='backend Redis decision samples',seconds=seconds,bucket_seconds=bucket,points=points)
    except (ValueError,TypeError): return jsonify(error='invalid time-series parameters'),400
    except Exception: return jsonify(error='redis_unavailable'),503

@api.get('/queue/status')
def queue_status(): return jsonify(depth=metrics().get_json().get('queue_depth',0))
@api.get('/worker/status')
def worker_status():
    try: return jsonify(status='healthy' if store().ttl(WORKER_KEY)>0 else 'stale',processed=int(store().get('ratelimit:worker:processed') or 0))
    except Exception: return jsonify(status='unavailable'),503

@api.get('/faults')
def get_faults():
    active=active_faults(); ttl={}
    try:
        for name in active: ttl[name]=max(0,store().ttl('ratelimit:fault:'+name))
    except Exception: pass
    return jsonify(enabled=current_app.config['FAULTS_ENABLED'],active=active,ttl_seconds=ttl)
@api.post('/faults')
def set_faults():
    if not admin(): return auth_failure()
    if not current_app.config['FAULTS_ENABLED']: return jsonify(error='fault_injection_disabled'),403
    d=request.get_json(silent=True) or {}; name=d.get('name'); enabled=bool(d.get('enabled',True))
    if name not in ('redis_unavailable','rabbitmq_unavailable','worker_unavailable','api_degraded','network_timeout','latency_ms'): return jsonify(error='unsupported_fault'),400
    try: duration=max(1,min(300,int(d.get('duration_seconds',60))))
    except (ValueError,TypeError): return jsonify(error='duration_seconds must be between 1 and 300'),400
    if name=='latency_ms':
        if not enabled: store().delete('ratelimit:fault:latency_ms'); return get_faults()
        try: value=max(0,min(2000,int(d.get('value',250))))
        except (ValueError,TypeError): return jsonify(error='latency_ms must be an integer'),400
        store().set('ratelimit:fault:latency_ms',str(value),ex=duration)
    else:
        try:
            if enabled: store().set('ratelimit:fault:'+name,'1',ex=duration)
            else: store().delete('ratelimit:fault:'+name)
        except Exception: return jsonify(error='redis_unavailable'),503
    return get_faults()
@api.delete('/faults')
def reset_faults():
    if not admin(): return auth_failure()
    FAULTS.clear()
    try:
        for name in ('redis_unavailable','rabbitmq_unavailable','worker_unavailable','api_degraded','network_timeout','latency_ms'): store().delete('ratelimit:fault:'+name)
    except Exception: return jsonify(error='redis_unavailable'),503
    return jsonify(active={})

@api.post('/benchmarks/run')
def benchmark():
    if not admin(): return auth_failure()
    d=request.get_json(silent=True) or {}; alg=d.get('algorithm','token_bucket')
    try: count=min(500,max(1,int(d.get('count',100))))
    except (TypeError,ValueError): return jsonify(error='count must be an integer from 1 to 500'),400
    old=config_value(); c=validate_config({'algorithm':alg}); store().set('ratelimit:config',json.dumps(c)); t=time.perf_counter(); accepted=0; lat=[]
    for i in range(count):
        with current_app.test_request_context('/api/requests/test',method='POST',json={'client_id':f'bench-{i%10}'}):
            before=time.perf_counter(); resp=test_request(); lat.append((time.perf_counter()-before)*1000); accepted+=resp.status_code==200
    elapsed=time.perf_counter()-t; c=old; store().set('ratelimit:config',json.dumps(c)); lat.sort()
    report={'id':str(uuid.uuid4()),'algorithm':alg,'offered':count,'duration_seconds':round(elapsed,3),'offered_rps':round(count/elapsed,2),'accepted':accepted,'rejected':count-accepted,'p50_ms':lat[len(lat)//2],'p95_ms':lat[min(len(lat)-1,int(len(lat)*.95))],'p99_ms':lat[min(len(lat)-1,int(len(lat)*.99))]}
    store().lpush('ratelimit:benchmarks',json.dumps(report)); return jsonify(report),201
@api.get('/benchmarks')
def benchmarks():
    try: return jsonify(items=[json.loads(x) for x in store().lrange('ratelimit:benchmarks',0,99)])
    except Exception: return jsonify(error='redis_unavailable'),503

@api.delete('/metrics')
def reset_metrics():
    if not admin(): return auth_failure()
    store().delete('ratelimit:metrics','ratelimit:latencies','ratelimit:request_times','ratelimit:history','ratelimit:chart_samples'); return jsonify(reset=True)
