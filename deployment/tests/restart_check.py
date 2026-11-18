"""Run INSIDE the disposable demo container; never against a live deployment."""
import json
import os
from pathlib import Path
import signal
import time
import urllib.request
import redis

r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
original_lease = r.get('ratelimit:publisher:lease')


def pids(script):
    matches = []
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            if script.encode() in path.read_bytes().split(b'\0'):
                matches.append(int(path.parent.name))
        except (OSError, ProcessLookupError):
            pass
    return matches


for role, script in [('worker', '/studio/worker/consumer.py'),
                      ('publisher', '/studio/worker/publisher.py')]:
    original = pids(script)
    assert len(original) == 1
    os.kill(original[0], signal.SIGKILL)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        current = pids(script)
        if len(current) == 1 and current != original:
            break
        time.sleep(.2)
    else:
        raise AssertionError(role + ' was not restarted')
    print('PASS: supervisor restarted ' + role, flush=True)

# A killed publisher cannot release its Redis lease; allow expiry, then require
# all dependency health to recover. This is a local simulation of a hard kill.
deadline = time.monotonic() + 150
while time.monotonic() < deadline:
    try:
        if r.get('ratelimit:publisher:lease') in (None, original_lease):
            time.sleep(2)
            continue
        with urllib.request.urlopen('http://localhost:10000/api/health', timeout=10) as response:
            assert json.load(response)['healthy'] is True
            print('PASS: all dependencies ready after process recovery', flush=True)
            break
    except (OSError, AssertionError):
        time.sleep(2)
else:
    raise AssertionError('Dependency readiness did not recover')
