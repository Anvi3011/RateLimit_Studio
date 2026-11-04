# API Reference

All endpoints are under `/api`. JSON request bodies are limited to 64 KiB. Administrative operations require `X-Admin-Token`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API, Redis, RabbitMQ, worker and publisher health |
| GET | `/metrics` | Observed totals, rates, latency percentiles, RabbitMQ depth and Redis outbox depth/inflight/failed/overflow, publish confirmations and retry counts |
| GET | `/metrics/timeseries?seconds=300&bucket_seconds=5` | Bounded real decision samples aggregated into chart buckets |
| DELETE | `/metrics` | Admin-only reset of metric/history samples (rate state remains) |
| GET | `/history?page=1&page_size=50&decision=accepted&client_id=x&endpoint=/x&since=unix_timestamp` | Bounded filtered decisions |
| GET | `/config` | Active policy |
| PUT | `/config` | Admin-only policy update |
| POST | `/requests/test` | Make one real rate-limit decision; returns 200 or 429 |
| POST | `/traffic/generate` | Bounded traffic batch: `{ "count": 10, "requests_per_second": 20, "client_id": "demo" }` |
| GET | `/queue/status` | RabbitMQ queue depth |
| GET | `/worker/status` | Worker heartbeat and processed count |
| GET | `/faults` | Fault feature state |
| POST | `/faults` | Admin-only controlled test fault |
| DELETE | `/faults` | Admin-only clear all shared temporary fault toggles |
| POST | `/benchmarks/run` | Admin-only bounded sequential sample |
| GET | `/benchmarks` | Stored sample reports |

Policy fields: `algorithm` (`token_bucket` or `sliding_window`), `rate` (1–100000 requests/second), `burst` (1–100000), `window_seconds` (1–3600), `edge_nodes` (1–100), `identity_strategy` (`header` or `ip`), `endpoint` (up to 256 characters), and `policy_id` (letters/numbers/underscore/hyphen). Sliding Window allows `rate * window_seconds` requests in the rolling duration.

Request response includes `request_id`, `timestamp`, `decision`, `status`, `latency_ms`, `remaining`, and `retry_after`. A rate-limited request receives status 429 plus `Retry-After` and `X-RateLimit-Remaining`. On Redis failure, fail-closed returns 503 (infrastructure error); fail-open returns 200 with the failure reason recorded. Events are synchronously admitted to the bounded Redis outbox after the decision; the publisher relay sends durable messages with broker confirms. Full or unavailable outbox state is reported in `event_publish_error`. Broker retries and exhaustion are visible through `/metrics`.

Fault names currently supported: `redis_unavailable`, `rabbitmq_unavailable`, `worker_unavailable`, `api_degraded`, `network_timeout`, and `latency_ms`. Use `{ "name": "network_timeout", "enabled": true, "duration_seconds": 30 }`. Faults require `FAULTS_ENABLED=true` and a valid admin token. Durations default to 60 seconds and are clamped to 1–300 seconds; Redis TTL automatically cleans up expired fault state. The network-timeout scenario delays the HTTP request beyond the frontend Nginx upstream read timeout, returning a proxy timeout while the bounded handler completes. `GET /faults` reports active fault TTLs. Five invalid admin attempts per client per 60 seconds return 429; Nginx also rate limits sensitive control endpoints.
