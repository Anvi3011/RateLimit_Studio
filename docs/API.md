# API Reference

All endpoints are under `/api`. JSON request bodies are limited to 64 KiB. All demonstration endpoints are completely public; operations and configurations are isolated per visitor using automatic, cryptographically signed demo sessions.

| Method | Path | Purpose |
|---|---|---|
| GET | `/session` | Retrieve or create isolated demo session (`X-Demo-Session-ID`) |
| GET | `/health` | API, Redis, RabbitMQ, PostgreSQL, worker, and publisher health |
| GET | `/metrics` | Session-isolated observed totals, rates, latency percentiles, RabbitMQ depth, and outbox metrics |
| GET | `/metrics/timeseries?seconds=300&bucket_seconds=5` | Bounded real decision samples aggregated into chart buckets |
| DELETE | `/metrics` | Reset session metrics and in-memory sample cache |
| GET | `/history?page=1&page_size=50&decision=accepted` | Session in-memory decision history |
| GET | `/config` | Session-isolated active policy |
| PUT | `/config` | Public update of session policy (persisted to Redis & PostgreSQL audit) |
| POST | `/requests/test` | Make one real rate-limit decision; returns 200 or 429 |
| POST | `/traffic/generate` | Bounded traffic batch: `{ "count": 10, "requests_per_second": 20, "client_id": "demo" }` |
| GET | `/queue/status` | RabbitMQ queue depth |
| GET | `/worker/status` | Worker heartbeat and processed count |
| GET | `/faults` | Active session fault simulation status |
| POST | `/faults` | Trigger controlled test fault for current demo session |
| DELETE | `/faults` | Clear active fault simulations for current demo session |
| POST | `/benchmarks/run` | Run bounded sequential benchmark for current demo session |
| GET | `/benchmarks` | Stored benchmark reports |
| GET | `/logs/summary` | Summary counts calculated from PostgreSQL records for session |
| GET | `/logs/requests` | Paginated structured PostgreSQL request logs with filters |
| GET | `/logs/requests/<id>` | Full request detail record from PostgreSQL |
| GET | `/logs/configurations` | Historical audit log of policy updates from PostgreSQL |
| GET | `/logs/faults` | History of simulated faults and recovery times from PostgreSQL |
| GET | `/logs/system-events` | System events filtered by severity and component |
| GET | `/logs/requests/export.csv` | Safe CSV download with formula injection protection |
| POST | `/logs/cleanup` | Purge expired sessions and their associated logs |

Policy fields: `algorithm` (`token_bucket` or `sliding_window`), `rate` (1–100000 requests/second), `burst` (1–100000), `window_seconds` (1–3600), `edge_nodes` (1–100), `identity_strategy` (`header` or `ip`), `endpoint` (up to 256 characters), and `policy_id` (letters/numbers/underscore/hyphen). Sliding Window allows `rate * window_seconds` requests in the rolling duration.

Request response includes `request_id`, `timestamp`, `decision`, `status`, `latency_ms`, `remaining`, and `retry_after`. A rate-limited request receives status 429 plus `Retry-After` and `X-RateLimit-Remaining`. On Redis failure, fail-closed returns 503 (infrastructure error); fail-open returns 200 with the failure reason recorded. Events are synchronously admitted to the bounded Redis outbox after the decision; the publisher relay sends durable messages with broker confirms. Full or unavailable outbox state is reported in `event_publish_error`. Broker retries and exhaustion are visible through `/metrics`.

Fault names currently supported: `redis_unavailable`, `rabbitmq_unavailable`, `worker_unavailable`, `api_degraded`, `network_timeout`, and `latency_ms`. Use `{ "name": "network_timeout", "enabled": true, "duration_seconds": 30 }`. Faults require `FAULTS_ENABLED=true` and a valid admin token. Durations default to 60 seconds and are clamped to 1–300 seconds; Redis TTL automatically cleans up expired fault state. The network-timeout scenario delays the HTTP request beyond the frontend Nginx upstream read timeout, returning a proxy timeout while the bounded handler completes. `GET /faults` reports active fault TTLs. Five invalid admin attempts per client per 60 seconds return 429; Nginx also rate limits sensitive control endpoints.
