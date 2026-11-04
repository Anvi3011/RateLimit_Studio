"""Run a matched short local comparison; writes measured results only."""
import csv, json, pathlib, re, subprocess, urllib.request, urllib.parse, time

ROOT=pathlib.Path(__file__).resolve().parents[1]
REPORTS=ROOT/'benchmarks'/'reports'
BASE='http://localhost:8080'

def read_admin_token():
    for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
        if line.startswith('ADMIN_TOKEN='): return line.partition('=')[2]
    raise RuntimeError('ADMIN_TOKEN missing from .env')

TOKEN=read_admin_token()
def api(path,method='GET',body=None):
    data=None if body is None else json.dumps(body).encode()
    request=urllib.request.Request(BASE+path,data=data,method=method,headers={'Content-Type':'application/json','X-Admin-Token':TOKEN})
    with urllib.request.urlopen(request,timeout=10) as response: return json.loads(response.read())

def parse_locust_output(output):
    output=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',output)
    rows=[]
    for line in output.splitlines():
        if 'Aggregated' not in line or not re.search(r'\d+\s+\d+\([\d.]+%\)',line): continue
        values=re.findall(r'\d+(?:\.\d+)?',line)
        if len(values)>=9: rows.append(values[:9])
    if not rows: raise RuntimeError('Could not parse final Locust aggregate; refusing to create a benchmark report')
    count,failures,failure_pct,average,min_ms,max_ms,median,rps,failures_s=rows[-1]
    section=output.rsplit('Response time percentiles (approximated)',1)[-1]
    percentile_rows=[]
    for line in section.splitlines():
        if line.strip().startswith('POST     rate-limit decision') or line.strip().startswith('POST rate-limit decision'):
            values=re.findall(r'\d+(?:\.\d+)?',line)
            if len(values)>=11: percentile_rows.append(values)
    if not percentile_rows: raise RuntimeError('Could not parse Locust percentile summary')
    percentiles=percentile_rows[-1]
    return {'request_count':int(count),'failure_count':int(failures),'failure_rate_pct':float(failure_pct),'average_ms':float(average),'min_ms':float(min_ms),'max_ms':float(max_ms),'median_ms':float(median),'p50_ms':int(percentiles[0]),'p95_ms':int(percentiles[5]),'p99_ms':int(percentiles[7]),'requests_per_second':float(rps),'failures_per_second':float(failures_s)}

def decisions_since(prefix, started):
    counts={'total':0,'accepted':0,'rejected':0,'errors':0}; page=1
    while True:
        query=urllib.parse.urlencode({'client_id':prefix,'since':started,'page':page,'page_size':100})
        rows=api('/api/history?'+query)['items']
        if not rows: break
        for row in rows:
            counts['total']+=1
            if row['decision']=='accepted': counts['accepted']+=1
            elif row['decision']=='rejected': counts['rejected']+=1
            elif row['decision']=='error': counts['errors']+=1
        page+=1
    return counts

def main():
    REPORTS.mkdir(parents=True,exist_ok=True)
    original=api('/api/config'); results=[]
    try:
        for algorithm in ('token_bucket','sliding_window'):
            policy={**original,'algorithm':algorithm,'rate':5,'burst':10,'window_seconds':1,'policy_id':'benchmark-'+algorithm}
            saved=api('/api/config','PUT',policy)
            if saved!=policy: raise RuntimeError(f'backend did not return stored policy for {algorithm}')
            before=api('/api/metrics'); started=time.time(); prefix='benchmark-'+algorithm
            name=algorithm.replace('_','-')
            command=['docker','compose','--profile','benchmarks','run','--rm','--no-deps','-e','LOCUST_CLIENT_PREFIX='+prefix,'locust','--headless','--host','http://nginx','-u','20','-r','5','-t','10s','--csv',f'/reports/{name}']
            completed=subprocess.run(command,cwd=ROOT,check=True,capture_output=True,text=True)
            output=completed.stdout+'\n'+completed.stderr
            print(output,end='')
            after=api('/api/metrics')
            stats=parse_locust_output(output)
            decisions=decisions_since(prefix,started)
            results.append({'algorithm':algorithm,'policy':saved,'workload':{'users':20,'spawn_rate':5,'duration':'10s'},'locust_completed_requests':stats,'api_decisions_for_run_prefix':decisions,'global_api_counter_delta':{key:after.get(key,0)-before.get(key,0) for key in ('total','accepted','rejected','errors')}})
    finally:
        api('/api/config','PUT',original)
    report={'environment':'local Docker Compose','host':BASE,'locust_image':'locustio/locust:2.32.4','metrics_scope':'Locust completed-response totals and backend decisions are reported separately. API decisions are filtered by each run’s unique client prefix and start timestamp; requests still in flight when Locust stops may make API decisions exceed completed responses.','results':results}
    target=REPORTS/'local-comparison.json'; target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (REPORTS/'local-comparison.csv').open('w',newline='',encoding='utf-8') as destination:
        columns=['algorithm','locust_completed_requests','locust_failures','requests_per_second','average_ms','p50_ms','p95_ms','p99_ms','api_decisions','accepted','rejected','errors']
        writer=csv.DictWriter(destination,fieldnames=columns); writer.writeheader()
        for item in results:
            stats=item['locust_completed_requests']; counts=item['api_decisions_for_run_prefix']
            writer.writerow({'algorithm':item['algorithm'],'locust_completed_requests':stats['request_count'],'locust_failures':stats['failure_count'],'requests_per_second':stats['requests_per_second'],'average_ms':stats['average_ms'],'p50_ms':stats['p50_ms'],'p95_ms':stats['p95_ms'],'p99_ms':stats['p99_ms'],'api_decisions':counts['total'],'accepted':counts['accepted'],'rejected':counts['rejected'],'errors':counts['errors']})
    print(target)

if __name__=='__main__': main()
