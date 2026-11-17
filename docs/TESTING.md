# Local testing and verification

## Backend and publisher tests

Use an isolated local Docker Compose stack with the named Redis/RabbitMQ volumes intact. The tests do not run `FLUSHDB`; the time-series and fault tests restore or remove only the keys they changed. The backend integration suite also updates and restores the active policy, and its request tests can append normal request history and metrics. For a strict no-data-change run, point the suite at a disposable Redis volume rather than a shared user stack. Never use production or shared data for these tests.

```sh
docker compose up --build -d
docker compose exec -T backend python -m pytest -q /app/tests
docker compose exec -T worker python -m pytest -q /worker/tests
```

The backend suite covers the existing rate-limit and API regressions plus Redis-backed auth throttling, shared fault TTL and cleanup, the measured metrics endpoint, and outbox capacity. The worker suite covers existing event idempotency plus outbox retry attempt accounting, failed-list exhaustion, and inflight recovery.

## Browser-driven tests

Start a private local stack with `FAULTS_ENABLED=true` only for this isolated test run:

```powershell
# In .env for this isolated local stack, temporarily set FAULTS_ENABLED=true.
$env:FAULTS_ENABLED = 'true'
docker compose up --build -d
docker compose --profile e2e run --build --rm e2e
$env:FAULTS_ENABLED = 'false'
docker compose up -d backend
```

The optional `e2e` profile supplies Chromium and Playwright; it saves failure artifacts under `frontend/test-results/`. For host-driven execution instead, run `npm install`, `npx playwright install chromium`, set `PLAYWRIGHT_BASE_URL`, and run `npm run test:e2e` inside `frontend/`. No admin token is required.

The Playwright flow edits and applies a policy, verifies dashboard refresh and reload persistence, generates traffic, inspects actual decision rows, toggles/restores a fault, and checks a real proxy timeout followed by automatic expiry and recovery. It restores the original policy and previously active fault names. Do not point it at production or a shared Redis database.

`FAULTS_ENABLED=true` is required for the fault-control portion. The proxy-induced timeout test waits for the backend's bounded six-second delay; set the Playwright timeout generously and leave the test environment idle while it recovers. Restore the local setting to false after the suite.

## Repeatable algorithm comparison

Run only against the local Compose stack:

```sh
python benchmarks/run_comparison.py
```

The runner uses both algorithms with the same rate 5/sec, burst 10, 20 Locust users, 5 users/sec spawn rate, and 10-second duration. It restores the prior policy and writes measured output to `benchmarks/reports/local-comparison.csv` and `.json`. Locust-completed responses and backend decisions are reported separately because requests can still be in flight at stop. Preserve the generated files with the run date and host details when publishing measurements.

## Additional checks

```sh
docker compose config --quiet
python -m compileall -q backend worker benchmarks
cd frontend && npm run build
```

## Executed verification (2026-11-05)

- Backend Pytest: 20 passed.
- Worker/publisher Pytest: 8 passed after regression tests for Pika 1.3.2 confirm behavior and retry-exhaustion recovery.
- Playwright: 1 browser flow passed, including policy persistence, real measured chart data, inspector, fault controls, timeout, expiry, and recovery.
- Frontend production build and `docker compose config --quiet`: passed. Backend, worker, publisher, frontend, and Locust images built on the local `linux/x86_64` Docker engine; the optional Playwright image build was stopped during its 786 MB base-image download. Deployment shell scripts passed `bash -n` in a local Linux container.
- Default Nginx and frontend Nginx config checks: passed. TLS Nginx config check: passed. A temporary self-signed local TLS smoke test verified HTTP 301 redirect, HTTPS `/api/health` 200, and HSTS. The temporary certificate files were removed. This does not validate a public certificate or renewal.
- Both algorithms were rerun with matched 20-user, 5 users/sec spawn, 10-second workloads. Token bucket: 2,562 completed requests, 0 failures, 268.02 req/s, avg 51 ms, p50 56 ms, p95 80 ms, p99 90 ms. Sliding window: 1,891 completed requests, 0 failures, 195.22 req/s, avg 75 ms, p50 79 ms, p95 120 ms, p99 170 ms. Raw measured CSV and JSON are preserved in `benchmarks/reports/local-comparison.csv` and `.json`. These are local demo measurements, not AWS capacity claims.

### Final publishing preflight (2026-11-05)

- Rebuilt backend and worker images from the current staged source without restarting the running services. Backend Pytest: 20 passed; worker/publisher Pytest: 8 passed.
- A fresh frontend Docker build ran `npm ci` and `npm run build` successfully; npm reported 0 vulnerabilities.
- `docker compose config --quiet` passed. The existing seven-service stack remained up, and `/api/health` reported API, Redis, RabbitMQ, worker, and publisher healthy.
- The browser flow was not rerun in this preflight because fault injection is disabled in the running app and the optional E2E image is not present. The earlier browser flow result above remains the last executed E2E result.

## Interactive sliders and independent local operation (2026-11-17)

Run from the repository root with the existing `.env` (never overwrite initialized-volume credentials):

```powershell
docker compose config --quiet
docker compose up --build -d
$env:PYTHONPATH = "$PWD\backend;$PWD\worker"
python -m pytest backend/tests worker/tests -q
npm --prefix frontend run test:e2e
```

Host-side Python tests require the dependencies in backend/requirements.txt and worker/requirements.txt; use a virtual environment. Playwright requires frontend npm dependencies and an installed Chromium browser. Runtime use needs only Docker Desktop.

Verified during this change:

- React production build on host and in Docker.
- Sliders: keyboard movement, numeric synchronization, invalid draft rejection, unsaved edits surviving polling, explicit Apply, and sliding-window quota conversion back to the existing rate API.
- Traffic Inspector generation and decision display, fault slider disabled states, and a 390px viewport.
- Browser requests restricted to localhost; all attempted external requests blocked and none observed.
- Live Docker traffic batches of 10, 100 and 137 requests; individual HTTP 200/429 records; accepted/rejected counts matched PostgreSQL and metrics.
- Sliding Window at rate 2 and window 1 second: 2 accepted and 8 rejected in a 10-request batch.
- PostgreSQL SQL count and CSV export both contained 257 request records for the verification session; a separate browser session had zero records.
- Logs, saved configuration and configuration audit history survived a PostgreSQL/backend/worker restart.
- Fault activation, local Redis state, PostgreSQL fault audit, and manual recovery in a separate Flask test client using the real local stores. Public server FAULTS_ENABLED was left false.
- Redis PONG; RabbitMQ event queue drained with a consumer and zero unacknowledged messages; all eight default services healthy.
- Existing-volume PostgreSQL credential mismatch reproduced and repaired after a local pg_dump backup, by aligning the existing role password with .env. No volumes/tables/queues were deleted.

Unit tests use SQLite/fakeredis where configured by existing fixtures; the live integration checks above used actual PostgreSQL, Redis and RabbitMQ. Full host internet disconnection and AWS deployment were not tested. The optional AWS/TLS paths were preserved; no AWS resources were accessed or modified. CSV export is bounded to 1,000 latest session records; normal API pages remain capped at 100. Slow traffic is synchronous, so keep the page open until it completes.

Final results for this pass: **42 Python tests passed**, **2 Playwright tests passed**, Docker Compose configuration validated, and both running Nginx configurations passed syntax checks. The live lower-RPS check (5 requests at 1 RPS) completed in 4.11 seconds beyond the old proxy timeout; the maximum 500-request batch at offered 200 RPS completed in 5.81 seconds. Offered RPS is a target, not a claim of achieved throughput.

## Fault recovery audit reconciliation (2026-11-18)

Redis TTL expiry previously removed the live fault key without updating its PostgreSQL audit row. Only explicit Restore updated audit records, and repeated restores could create duplicate recovery events.

The backend now sweeps due ACTIVE fault records every two seconds and once at worker startup, independently of browser requests and FAULTS_ENABLED. It uses Redis server time, checks that the current fault key is absent, and changes matching rows to EXPIRED / AUTO_RECOVERED with the persisted expiry deadline as recovery_timestamp. The Fault History API also reconciles the requesting session before reading history. Redis read failures leave records pending and are retried; a live Redis key prevents premature recovery of the latest record.

Injection and recovery serialize per demo-session/fault-type using PostgreSQL transaction advisory locks and row locks. A new injection closes the previous live generation as CLEARED / SUPERSEDED; manual Restore uses CLEARED / MANUAL_RECOVERED. Repeated restores do not rewrite terminal records/timestamps or append duplicate recovery events. Restoring an already-expired fault retains the automatic cause. Terminal events include the specific fault audit ID. Existing overdue rows are repaired in place; no table migration, volume reset, queue deletion, extra service, Redis notification configuration, AWS dependency, or admin token is required.

The existing table columns are sufficient; original IDs, session IDs, injection times, deadlines, and descriptions are preserved. Already-terminal audit rows are left unchanged. Fault History renders completed recovery statuses in green and displays the stored recovery timestamp.

Focused verification commands (the browser test requires local FAULTS_ENABLED=true):

```powershell
$env:PYTHONPATH = "$PWD\backend;$PWD\worker"
python -m pytest backend/tests/test_fault_recovery.py -q
python -m pytest backend/tests worker/tests -q
npm --prefix frontend run test:e2e -- fault-recovery.spec.js
docker compose build backend frontend
docker compose up -d --no-deps backend frontend
Invoke-RestMethod http://localhost:8080/api/health
```

Reconciliation is eventual (normally within two seconds while the backend and both stores are available), not a Redis key-expiry notification. It catches up after backend downtime or dependency outages. Fault rows with no recorded deadline cannot be inferred as expired; they require explicit restoration. Redis and PostgreSQL do not share a distributed transaction: an operation that loses the database connection after mutating Redis returns an error and should be retried; deadline reconciliation still provides eventual expiry cleanup. The standard Compose/Gunicorn startup (without --preload) starts the reconciler in each worker; any custom preloaded Gunicorn deployment must start it in a post-worker-init hook instead.

Verification results for the fault recovery fix: **51 Python tests passed** (including nine new lifecycle tests); **one targeted Playwright fault-history test passed**; backend/frontend Docker builds and React production build passed. Only backend/frontend were rebuilt and recreated, and only backend was stopped/started for the restart test. `/api/health` reported every dependency healthy, all eight services were healthy, and RabbitMQ retained its event/dead-letter queues with an active consumer and no pending messages.

Live SQL checks observed a five-second worker fault as ACTIVE/PENDING, then EXPIRED/AUTO_RECOVERED with recovery_timestamp equal to its stored expiry deadline, using only SQL polling while waiting for expiry (no browser/history refresh). Separate manual Restore and repeated Restore produced one CLEARED/MANUAL_RECOVERED row and one matching recovery event. A fault expiring during backend downtime was repaired on restart. A before/after snapshot confirmed all 50 existing fault rows remained, all 25 pre-existing terminal rows were unchanged in every stored field, 25 overdue ACTIVE rows were repaired, configuration history was unchanged, and request logs were retained. The UI browser test independently verified both recovered states and populated timestamps.

Recreating the backend exposed local `.env` PostgreSQL/RabbitMQ credentials that differed from the verified working runtime credentials in the untouched worker. The local ignored `.env` was restored to those verified credentials; database/broker passwords and other services were not changed. No secrets were printed or committed.
