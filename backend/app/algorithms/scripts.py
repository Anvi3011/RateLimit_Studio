TOKEN_BUCKET = r'''
local now=tonumber(ARGV[1]); local rate=tonumber(ARGV[2]); local cap=tonumber(ARGV[3]); local cost=tonumber(ARGV[4]); local ttl=tonumber(ARGV[5])
local values=redis.call('HMGET',KEYS[1],'tokens','ts'); local tokens=tonumber(values[1]) or cap; local ts=tonumber(values[2]) or now
tokens=math.min(cap,tokens+math.max(0,now-ts)*rate/1000); local allowed=0
if tokens>=cost then tokens=tokens-cost; allowed=1 end
redis.call('HSET',KEYS[1],'tokens',tokens,'ts',now); redis.call('EXPIRE',KEYS[1],ttl)
local retry=0; if allowed==0 and rate>0 then retry=math.ceil((cost-tokens)/rate) end
return {allowed,math.floor(tokens),retry}
'''

SLIDING_WINDOW = r'''
local now=tonumber(ARGV[1]); local window=tonumber(ARGV[2]); local limit=tonumber(ARGV[3]); local member=ARGV[4]
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-window*1000)
local count=redis.call('ZCARD',KEYS[1]); local allowed=0; local retry=0
if count<limit then redis.call('ZADD',KEYS[1],now,member); count=count+1; allowed=1
else local first=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES'); if first[2] then retry=math.max(1,math.ceil((tonumber(first[2])+window*1000-now)/1000)) end end
redis.call('EXPIRE',KEYS[1],window+1)
return {allowed,math.max(0,limit-count),retry}
'''
