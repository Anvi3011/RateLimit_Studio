import os, uuid
from locust import HttpUser, task, between

class RateLimitUser(HttpUser):
    wait_time=between(0.0,0.02)
    def on_start(self): self.client_id=f'{os.getenv("LOCUST_CLIENT_PREFIX","locust")}-{uuid.uuid4().hex}'
    @task
    def decision(self):
        with self.client.post('/api/requests/test',json={'client_id':self.client_id,'edge_id':f'edge-{uuid.uuid4().int%5}'},catch_response=True,name='rate-limit decision') as response:
            # HTTP 429 is an expected algorithm outcome; 5xx/network errors are failures.
            if response.status_code in (200,429): response.success()
            else: response.failure(f'infrastructure response {response.status_code}')
