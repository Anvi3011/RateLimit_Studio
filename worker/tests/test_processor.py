from processor import process

class FakeRedis:
    def __init__(self): self.values=set(); self.count=0; self.marker_ttls={}
    def set(self,key,value,nx=False,ex=None):
        if nx and key in self.values: return False
        self.values.add(key); self.marker_ttls[key]=ex; return True
    def hincrby(self,*_): self.count+=1
    def incr(self,*_): self.count+=1

def test_duplicate_events_have_one_effect():
    r=FakeRedis(); event={'event_id':'evt-1','decision':'accepted'}
    assert process(event,r) is True
    assert r.marker_ttls['ratelimit:event:processed:evt-1']==604800
    assert process(event,r) is False
    assert r.count==2

def test_replay_after_idempotency_marker_expiry_reapplies_worker_counters():
    r=FakeRedis(); event={'event_id':'evt-expired','decision':'rejected'}
    assert process(event,r) is True
    r.values.clear(); r.marker_ttls.clear()  # model the seven-day marker expiring
    assert process(event,r) is True
    assert r.count==4
