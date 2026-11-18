# Dual-deployment audit — Phase 1

Audit date: 18 November 2026. Branch: `deployment/dual-mode`. Baseline commit: `71c71a7`.

This is an audit and proposed plan, not an implemented deployment. Only this document was added. No infrastructure was provisioned, no deployment scripts were executed, no commits were pushed, and no volumes or databases were reset. React source and CSS remain unchanged.

## 1. Repository identity and scope

The IDE workspace contains two distinct Git repositories:

- Workspace parent: `C:/Users/LAVANYA/MyProjects/RateLimit_Studio`, on `add-logging`, with nested repositories untracked.
- **Requested repository:** `C:/Users/LAVANYA/MyProjects/RateLimit_Studio/RateLimit_Studio`, on `deployment/dual-mode`, initially clean. All paths below are relative to this nested repository. Configure future commands and hosting project roots accordingly; do not accidentally deploy the parent copy.

The audit covered tracked application code, Compose, Dockerfiles, proxy configuration, environment variable names, Python and browser tests, benchmark tooling, and existing architecture/deployment/operations documentation. Generated artifacts, Git internals, dependency trees and database backup contents are not application configuration and were not treated as authoritative source. Secret values were not printed or copied. No AGENTS.md was found inside the target repository.

| Area | Current implementation |
| --- | --- |
| Frontend | React/Vite in `frontend/src/main.jsx`; CSS in `frontend/src/styles/app.css`; frontend Nginx serves the build |
| API | Flask application factory, Gunicorn, session resolution and CSRF protection |
| State and limiting | redis-py TCP client, Lua decisions, session configuration, metrics, history and outbox |
| Messaging | Pika AMQP, durable RabbitMQ queue, publisher confirms and dead-letter queue |
| Persistence | SQLAlchemy/psycopg2 PostgreSQL logging; separate backend and worker database modules |
| Operations | Compose, EC2 shell scripts, optional TLS edge, backup and rollback scripts |
| Verification | pytest, Playwright, Locust and comparison/export scripts; no tracked CI workflow or Vercel/Render manifest |

## 2. Existing Compose deployment

The default stack has all **eight required services**: `postgres`, `rabbitmq`, `redis`, `worker`, `publisher`, `backend`, `frontend`, `nginx`. Additional services `locust`, `e2e`, and `nginx-tls` are profile gated and are not replacements for the eight-service baseline.

- PostgreSQL 16 uses `postgres_data`; Redis 7.4 uses AOF and `redis_data`; RabbitMQ 4 uses `rabbitmq_data`.
- Internal services use an internal Docker network. Only the edge proxy publishes a host port, default 8080. Nginx also joins the edge network.
- Backend/worker URLs are assembled with Docker hostnames. Publisher receives Redis and AMQP URLs; it does not need PostgreSQL credentials.
- Most dependencies wait for health; backend waits only for publisher startup. Frontend and edge Nginx have no explicit container health checks.
- Backend and worker use Python 3.12 slim images. Backend starts one synchronous Gunicorn worker on fixed port 5000 with a 30-second timeout. Frontend builds Vite and serves it with Nginx.
- Current TLS scripts stop `nginx` and start `nginx-tls`. That does not satisfy a literal requirement that all eight named services remain running in TLS mode. Future work should mount TLS configuration onto the existing `nginx` service through an override, preserving its name. The present HTTP default already preserves all eight.
- Keep the existing Compose project name and all three volume identities through updates and rollback. Never run volume removal, database reset, or pruning as part of this project.

## 3. Complete browser/request/logging flow

1. Browser loads React through edge Nginx, then frontend Nginx. Requests to `/api/*` pass through both proxies to Flask.
2. `backend/app/__init__.py` resolves a session before each request. `session.py` validates an HMAC-signed cookie or generates a new session, and attempts to touch its PostgreSQL session row.
3. Configuration comes from `ratelimit:config:<session>` in Redis, with a session-specific policy ID. Client identity comes from configured IP/header strategy or request JSON; the displayed edge ID is simulated, not a separate deployed edge service.
4. `POST /api/requests/test` calls `backend/app/redis/__init__.py:decide`. One atomic Lua script decides against the client/endpoint/policy key. Normal results are HTTP 200 or 429; Redis failure defaults to fail-closed HTTP 503, with optional fail-open behavior.
5. The API writes session metrics, bounded recent history/latencies, request times and chart samples to Redis. These writes are best effort and separate from the decision and outbox write.
6. The API atomically checks the Redis outbox capacity and appends an event containing the request ID, session ID, timestamps, configuration, decision and status. It returns without waiting for PostgreSQL persistence.
7. `worker/publisher.py` moves an event from `ratelimit:outbox` into `:inflight`, publishes to RabbitMQ with persistent message properties and mandatory routing, waits for confirms, then removes the inflight entry. After a crash it recovers inflight entries.
8. `worker/consumer.py` consumes `ratelimit.events`, prefetch 32, with explicit ACK/NACK. `processor.py` sets a seven-day Redis deduplication marker and writes `request_logs` in PostgreSQL, then increments worker counters. The consumer ACKs successful processing.
9. `/api/logs/*` queries PostgreSQL by the signed visitor session. This is real relational logging, distinct from Redis `/history` and dashboard metrics. Configuration changes, session/system events and fault histories are written synchronously by the backend database service.

Traffic generation and benchmarks call the request handler internally with the same session. They execute inside an HTTP request rather than becoming background queue jobs. Traffic accepts up to 500 requests at up to 200 rps; low requested rates can hold the HTTP request far beyond proxy/Gunicorn timeouts. Preserve functionality, but validate execution budgets before public use.

### Existing logging reliability gaps

These are source findings, not fixes made in Phase 1:

- `processor.save_to_db()` returns `False` if obtaining a database session fails, but `process()` ignores that result. The event can be counted and ACKed without a saved row. A missing database configuration also silently falls back to in-memory SQLite in both database modules. Production must explicitly require PostgreSQL.
- The Redis dedup marker is set **before** the database commit. A process crash between those operations can cause a redelivery to be skipped and ACKed without persistence. Database request-ID uniqueness helps duplicate handling but does not close this missing-row window.
- Database write exceptions remove the marker, but the consumer NACKs with `requeue=False`, moving the event to the dead-letter queue rather than automatically retrying. Recovery/replay needs a deliberate procedure and outage tests.
- If Redis is actually unavailable, the API may return a decision/error without being able to enqueue its log. Outbox overflow likewise does not change the HTTP decision. Injected RabbitMQ failure deliberately bypasses enqueueing. Therefore the current system does not guarantee one PostgreSQL row for every response under every failure.
- Publisher retries stop after eight attempts; its failed list retains only 1,000 items. Pending capacity defaults to 5,000 and excludes inflight/failed records. Startup recovery can exceed the nominal pending bound. No automatic reconciliation is implemented.
- Publisher retry/published counters use global `ratelimit:metrics`, while `/metrics` reads several corresponding fields from session hashes. A visitor may see zeros despite global activity. Queue depth, outbox sizes and worker counters are global operational aggregates.
- Many backend database read helpers swallow errors and return empty results, so some log APIs can appear successful during a database outage.
- Backend and worker separately define the request-log model. `create_all()` creates missing tables but is not a migration mechanism; starting the worker first can leave backend-specific indexes absent. Use an explicit initialization order and a future migration strategy without resetting data.
- `/api/logs/cleanup` is publicly callable and operates on all expired sessions. It is not scheduled; continuous session touches extend expiry, so 24-hour retention is not a hard maximum age for active visitors' logs. Do not invoke cleanup during this audit or use it as a database reset.
- CSV route requests 1,000 records but the database helper clamps page size to 100. Preserve this as a documented existing limitation until separately addressed.

## 4. Redis Lua and RabbitMQ compatibility

Token Bucket uses `HMGET`, `HSET`, `EXPIRE`, millisecond timestamps and bounded refill. Sliding Window uses sorted-set trimming, cardinality, insertion, earliest-score lookup and expiry. Both operate atomically on a single key. Keys hash length-delimited client/endpoint input and include the session policy ID.

Other Redis usage includes hashes, lists, sorted sets, pipelines where applicable, `SET NX EX`, TTLs, the enqueue Lua script and the publisher's four-key failure/retry Lua script. Publisher list movement uses `BRPOPLPUSH` and `RPOPLPUSH`. Do not replace these with a non-atomic pop followed by push. Do not assume arbitrary Redis Cluster compatibility: multi-key operations need evaluation against the actual provider's key-routing constraints.

Upstash documents native TCP support, Lua scripting, and these list commands. Use its **TLS native Redis URL (`rediss://`)** with redis-py, not its REST endpoint/token as a Redis URL. This makes it a plausible fit, not a verified drop-in deployment: run both exact decision scripts, enqueue/retry scripts, list movement, TLS, reconnect and concurrency tests against a disposable approved database. Sources: [compatibility](https://upstash.com/docs/redis/overall/compatibility), [list commands](https://upstash.com/docs/redis/commands/list/overview), [scripting](https://upstash.com/docs/redis/commands/scripting/overview).

RabbitMQ requirements are AMQP 0-9-1 over TLS (`amqps://`), provider-assigned virtual host, durable queues/exchanges, publisher confirms, mandatory routing, explicit acknowledgments and dead-letter permissions. Use the provider's complete connection URL, preserving encoded credentials and vhost. The topology is `ratelimit.events`, `ratelimit.dlx` (direct), and `ratelimit.events.dead` bound with routing key `ratelimit.events`.

CloudAMQP Little Lemur is the closest free candidate because it runs RabbitMQ itself. Its published shared-plan limits include 20 connections, 10,000 queued messages and 1 million messages/month; queue lifecycle limits also apply. Provisioning availability and effective quotas must be verified in the account. Publisher currently opens a connection per event, while health and metrics each open broker connections; reuse and bounded timeouts need attention. The separate legacy helper `backend/app/rabbitmq/__init__.py` still tests a falsey publish return and is not the active outbox path; its behavior should be reconciled before future reuse. [CloudAMQP plans](https://www.cloudamqp.com/plans/)

## 5. Frontend sessions and API transport

React uses relative `fetch('/api' + path)` with `credentials: 'same-origin'`. CSV export is a relative `/api/logs/requests/export.csv` link. There is no existing environment-driven API base URL. Local storage is used for tour completion, not as the session authority.

The cookie is host-only, HttpOnly, SameSite=Lax, path `/`, and Secure when Flask sees HTTPS. Its lifetime follows `LOG_RETENTION_HOURS`. All backend replicas need the same stable `SESSION_SECRET`; environments need different secrets. Changing a secret invalidates visitor sessions. `.env.example` currently omits this required Compose variable, although the local environment has the variable name present; the audit did not copy its value.

CSRF protection rejects mutating requests marked `Sec-Fetch-Site: cross-site`, even when CORS allows the origin. CORS alone cannot make direct calls from a Vercel domain to a Render domain work with current cookies and credentials. Changing to cross-site cookies would also require frontend transport changes and browser-policy testing.

**Recommended: keep every frontend source file unchanged and use a Vercel external rewrite `/api/:path*` to the Render backend `/api/:path*`.** The browser then sees a single origin and retains existing fetch and CSV behavior. Vercel supports external rewrites as reverse proxies. Place API routing before any SPA fallback and prevent caching of personalized API responses. [Vercel rewrites](https://vercel.com/docs/routing/rewrites)

This must be validated with real browser cookies, Set-Cookie forwarding, Origin, Sec-Fetch-Site, HTTPS scheme, client IP and CSV downloads. Existing `ProxyFix(x_for=2, x_proto=1, x_host=1)` assumes the Compose proxy chain; Render/Vercel have a different chain. Configure trust from observed provider behavior, not guessed hop counts. Exact public origin allowlists remain necessary; never solve proxy issues by disabling CSRF or trusting arbitrary forwarding headers.

The UI polls six endpoints every three seconds and additional log endpoints every six seconds on the logs tab. On initial load, parallel requests can all arrive before a cookie exists, generating different temporary sessions. Verify a stable browser session and displayed data across reloads. Server/proxy changes should preserve the current UI exactly.

## 6. Tests and health checks

Audit verification used the existing Python suites with `DATABASE_URL=sqlite:///:memory:`, `REDIS_URL=redis://127.0.0.1:1/0`, `PYTHONPATH` pointing to this repository's backend and worker, bytecode disabled and pytest cache disabled. **39 passed in 18.66 seconds.** These settings avoid connecting to the existing PostgreSQL/Redis deployment. An initial root-level collection attempt lacked the import paths and failed; setting those paths resolved it.

Coverage includes configuration, public sessions, signatures/forgery, isolation, CSRF, CSV, fault controls, metrics, bounded outbox, algorithm boundaries/concurrency, publisher retries/confirm semantics, recovery and worker deduplication. Tests use fake Redis or SQLite when real services are unavailable; passing does not establish production PostgreSQL durability or RabbitMQ delivery.

Playwright's tracked `studio.spec.js` covers configuration, traffic, charts, inspector, logs and disabled fault activation. It does not prove exact PostgreSQL row reconciliation, and API/browser contexts use separate cookies. Locust and comparison scripts target the local proxy and mutate workload state; they were inspected but not run. No browser suite, live database integration, container startup, backup, restore or hosted compatibility test was run during this audit.

| Health mechanism | What it checks / limitation |
| --- | --- |
| PostgreSQL | `pg_isready`; not application schema or successful worker commits |
| Redis | PING; not Lua correctness or provider quota |
| RabbitMQ | node ping; not end-to-end event persistence |
| Worker | Redis last-seen TTL, 15 seconds; can remain healthy while every database write fails |
| Publisher | Redis last-seen TTL, 60 seconds; reports activity rather than successful delivery |
| API | Redis, RabbitMQ connectivity, worker/publisher TTLs and `SELECT 1`; **database status is omitted from the final healthy boolean** |
| EC2 script | HTTP success from aggregate health; inherits its database blind spot |
| Frontend/Nginx | no explicit Compose health checks |

Future health design should separate cheap process liveness from full readiness, include PostgreSQL in readiness, and avoid restarting a healthy API just because a worker is temporarily unavailable. Add bounded network/DB timeouts. Health requests currently touch database sessions; high-frequency external probes create unnecessary database activity.

## 7. Free hosting feasibility

Provider facts were checked against official documentation during this audit. Limits can change; confirm dashboard terms before provisioning.

| Component | Assessment |
| --- | --- |
| Vercel | Static Vite output plus API rewrite is suitable. Publish `frontend/dist` after a lockfile-based build. Check account eligibility, bandwidth and proxy request-duration limits before public workloads. |
| Render API | Free web services sleep after 15 minutes without inbound traffic, with cold startup around a minute. Filesystem is ephemeral. Configure a process listening on `0.0.0.0:$PORT`. [Free service limits](https://render.com/docs/free) |
| Render publisher/worker | Dedicated background worker services have no Free instance option. Continuous independent workers require paid compute. Queue activity does not make a sleeping HTTP service wake. [Free service types](https://render.com/docs/free), [worker model](https://render.com/docs/background-workers) |
| Neon | Appropriate real PostgreSQL store; Free has storage/compute quotas and scale-to-zero behavior. Copy a TLS connection string from the provider; pooled connections and small local SQLAlchemy pools should be evaluated. Cold database connections need bounded retries. Polling/session touches can prevent idling. [Pricing](https://neon.com/pricing), [official pooling documentation source](https://github.com/neondatabase/website/blob/main/content/docs/connect/connection-pooling.md) |
| Upstash | Free currently advertises 256 MB and 500,000 commands/month. The current polling/heartbeat design can exhaust this even without synthetic traffic. [Redis pricing](https://upstash.com/pricing/redis) |
| CloudAMQP | Little Lemur can support a small RabbitMQ demo under its quotas; confirm TLS/topology permissions and connection usage in integration tests. [Plans](https://www.cloudamqp.com/plans/) |

Concrete quota warning: the publisher issues an idle heartbeat and blocking pop about once per second. At that cadence these two commands alone imply roughly 5.2 million command submissions in 30 days; worker heartbeats, browser polling, decisions, outbox and deduplication add more. This is a code-derived estimate, not measured provider billing. Longer blocking waits/backoff and less frequent heartbeats are needed, with matching TTLs and shutdown behavior. Existing API Redis timeouts of 0.3 seconds to connect and 0.5 seconds for operations are also fragile across regions.

**Conclusion: a reliable, continuously processing public deployment cannot be promised for zero cost on the requested stack.** A free demonstration can be feasible for occasional visits after changes, but must accept suspension, cold starts and delayed processing, and stay within quotas. No cron pings or artificial traffic should be used as an availability strategy.

## 8. Safest proposed architectures

### A. EC2 Learner's Lab — self-contained

Browser -> existing `nginx` -> existing frontend -> backend -> local Redis -> publisher -> local RabbitMQ -> worker -> local PostgreSQL. Backend also writes synchronous audit tables to local PostgreSQL.

Retain all eight named services and all named volumes. Use HTTP only for a temporary non-sensitive lab demonstration; use the same `nginx` service with an HTTPS override for trusted TLS. Expose only edge HTTP/HTTPS and restrict SSH. Select instance size/storage from actual Lab allowances and observed memory/CPU; do not assume a particular instance type is permitted. Lab session shutdown, temporary credentials, public-IP changes and account resource cleanup make this unsuitable as the public demo's dependency. Back up through a separate, approved operation; the current backup script stops services and archives data plus `.env`, so it is not a harmless read-only diagnostic.

### B. Public — recommended dependable mode

Browser -> Vercel static React and same-origin `/api` rewrite -> Render web backend. API, one publisher and one worker use a dedicated Upstash database and CloudAMQP vhost; API and worker use a dedicated Neon database. Run publisher and consumer as **two paid Render background workers**, each from `worker/` with its own start command, and prefer an always-on paid API for predictable access. These public resources must have no dependency on Lab EC2, its URLs, credentials or volumes.

Keep one publisher initially: startup inflight recovery is designed for a singleton, and multiple publishers can interfere with each other's inflight entries. Use modest connection pools, colocated regions where available, verified TLS and quota-aware polling. Free data-service tiers may still be useful, but are conditional on measured usage.

### C. Public — optional zero-cost compromise

If zero spend is essential, evaluate one Render Free web service containing three supervised processes: Gunicorn, publisher and consumer. This preserves the processing roles while the instance is awake; it is a proposed new launch mode, not supported by the current Dockerfiles. Supervisor must reap processes, forward termination signals, stop all children, restart failed roles, and recover inflight work. Child processes must start once per container, never once per Gunicorn worker or request.

All roles sleep together. Persisted queue/outbox work waits until an HTTP visit wakes the service; RabbitMQ cannot wake it. Require explicit acceptance of these limitations and platform terms, and prove quota usage first. If supervisors/quotas/provider compatibility cannot support this safely, choose paid workers or defer the free public mode. Do not silently remove RabbitMQ, publisher, worker or PostgreSQL to claim a free deployment.

## 9. Exact proposed file changes after approval

This is the candidate implementation surface, not permission to edit it now. Separate correctness fixes from provider manifests for review.

| File(s) | Proposed purpose |
| --- | --- |
| `.env.example` | Add missing `SESSION_SECRET`; document required URLs, TLS, public origin and pool/timeouts without real values; distinguish Compose interpolation from hosted configuration |
| `docker-compose.yml` | Preserve eight defaults, networks and volumes; add only compatible runtime/health settings where necessary |
| New `deployment/aws/compose.tls.yml` | HTTPS override on existing `nginx`; avoid substituting its service name |
| `deployment/aws/deploy.sh`, `rollback.sh`, `update.sh` | Apply matching overrides and preserve project/volume identities; validate session secret; update health verification |
| `deployment/aws/healthcheck.sh` | Verify readiness including database, with bounded retries and redacted diagnostics |
| `deployment/nginx/default.conf`, `production.conf`, `frontend/nginx.conf` | Review proxy trust/scheme propagation and traffic request timeouts; retain UI delivery |
| `backend/Dockerfile` and optional new `backend/gunicorn.conf.py` | Render-compatible port/start settings, preserving Compose port behavior |
| `backend/app/__init__.py` | Environment-specific validated proxy trust/origins; production configuration validation; retain session/CSRF semantics |
| `backend/app/routes/api.py` | Correct readiness, bounded dependency calls and any approved observability/traffic budget fixes; preserve endpoints and response contracts |
| `backend/app/redis/__init__.py` | Configurable TLS-compatible network timeouts/retries; preserve atomic decisions |
| `backend/app/db/connection.py`, `worker/db.py` | Require PostgreSQL in deployed modes; tune pools/connect timeouts; explicit schema initialization strategy |
| `worker/processor.py`, `consumer.py` | Commit-before-ACK reliability, safe deduplication and dead-letter/retry behavior; bounded AMQP connections and recovery |
| `worker/publisher.py`, `health.py` | Quota-aware waits/heartbeats, robust reconnect/shutdown, singleton recovery and health semantics |
| `backend/app/db/service.py` | If approved, report DB read failures honestly and constrain cleanup access; avoid implying successful empty logs on outage |
| `backend/app/rabbitmq/__init__.py` | If retained for reuse, align confirm semantics with active publisher; not needed to replace the pipeline |
| New `frontend/vercel.json` | Same-origin API rewrite, SPA fallback and suitable security/cache headers; zero React/CSS changes |
| New `render.yaml` | Explicit web service and two worker definitions for dependable mode; secret references only |
| New `.env.public.example` | Sanitized public-mode variable contract; publisher receives no database secret |
| Optional new `deployment/public/Dockerfile`, `entrypoint.py` | Only if free combined-process mode is selected; real supervision and signal handling |
| `backend/tests/test_auth_flow.py`, `test_hardening.py`, `worker/tests/test_processor.py`, `test_publisher.py`; new outage/health integration tests | Proxy/session isolation, required PostgreSQL, commit/ACK failure windows, recovery, quota-compatible commands |
| `frontend/tests/e2e/studio.spec.js`, optional new deployment smoke tests | Test unchanged UI through proxies, stable cookies, two visitors, CSV and actual persisted rows |
| `docs/AWS_DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, `docs/KNOWN_LIMITATIONS.md`, `deployment/aws/README.md`, `README.md`; new `docs/PUBLIC_DEPLOYMENT.md` | Beginner setup, costs/limits, recovery and independent configuration; correct stale admin-token and missing-PostgreSQL descriptions |

`frontend/src/main.jsx`, all style files, chart/animation/navigation code and assets must remain unchanged. No frontend API-base edit is needed for the recommended proxy architecture. Lua decision scripts need compatibility tests, not an assumed rewrite. Requirements/lockfiles need changes only if an approved implementation actually adds a dependency; use existing pinned Python dependencies and npm lockfile first.

Environment ownership: backend gets `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`, stable `SESSION_SECRET`, exact `FRONTEND_ORIGIN`, fail/fault/retention/capacity and pool/timeouts; worker gets the three service URLs; publisher gets Redis, AMQP and capacity/timeouts. Vercel gets only a non-secret backend routing destination. Never put service passwords or session secrets into Vite build variables or frontend bundles. Existing `API_PORT` and `WORKER_ID` template names do not drive the shown container startup; the Compose `REDIS_URL` environment value is hardcoded despite its template entry.

## 10. Beginner-friendly implementation sequence and approval gates

1. **Approve architecture and budget.** Choose dependable separate workers or the sleeping free compromise. Confirm whether fault injection stays disabled (recommended default). No accounts or resources need creation for this decision.
2. **Record baseline.** Stay in this nested repository on `deployment/dual-mode`; record Compose project/volume names and current UI source hashes. Preserve existing data. Arrange a protected backup/restore drill separately, accounting for the backup script's downtime.
3. **Repair deployment-critical reliability locally.** Require real PostgreSQL, fix commit/ACK/dedup failure windows and database readiness, bound remote timeouts and verify proxy/session behavior. Add focused outage tests. Do not alter the UI.
4. **Prepare both configurations.** Keep the eight-service Compose default. Add same-service TLS overrides, sanitized templates, Render manifests and Vercel proxy rules. For a free compromise add a separately selected supervised runtime rather than changing Compose roles.
5. **Review concrete changes before deployment.** Check manifests, environment ownership, costs and test results. Compare React/CSS hashes and service/volume lists. Implementation approval does not automatically authorize provisioning or publishing.
6. **After deployment approval, create independent public resources.** In the provider dashboards choose regions and quotas, copy secrets privately into server environment settings, initialize Neon schema, verify Upstash TLS/scripts and RabbitMQ topology. Do not migrate/reset Lab data or reuse its credentials.
7. **Start processing roles, then API, then frontend.** Confirm successful test-event PostgreSQL commits before calling the pipeline healthy. Configure Vercel's root to `frontend`, lockfile build and `dist` output; verify every `/api` method reaches Render through the rewrite.
8. **Validate each mode independently.** Stop relying on the other environment. Check two browser sessions, cookie persistence, both algorithms, 200/429/503 behavior, configuration/fault/system logging, CSV, traffic and benchmark timing. Match request IDs to actual PostgreSQL rows using read-only checks. Test approved dependency outages and recovery in isolated test resources, including crash before DB commit and crash after commit before ACK.
9. **Validate operational limits.** Restart publisher/worker with queued work, inspect failed/dead-letter queues, measure command/connection quotas and long-request behavior. For the free option test an actual sleep/wake cycle and delayed backlog drain. For EC2 validate all eight services, TLS, Lab shutdown/restart and retained volumes.
10. **Document handoff.** Provide separate URLs, start/stop instructions, observed quotas, costs, backup/restore/replay procedures and rollback steps. Keep secrets out of documentation. Push commits or publish only on explicit instruction.

Approval requested at the end of Phase 1: accept this plan and choose the public compute model. No implementation or deployment has been performed.
