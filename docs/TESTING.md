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

Start a private local stack with `FAULTS_ENABLED=true` only for this isolated test run. Use a valid local `.env` admin token, then:

```powershell
# In .env for this isolated local stack, temporarily set FAULTS_ENABLED=true.
$env:FAULTS_ENABLED = 'true'
docker compose up --build -d
docker compose --profile e2e run --build --rm e2e
$env:FAULTS_ENABLED = 'false'
docker compose up -d backend
```

The optional `e2e` profile supplies Chromium and Playwright; it reads `ADMIN_TOKEN` from `.env` and saves failure artifacts under `frontend/test-results/`. For host-driven execution instead, run `npm install`, `npx playwright install chromium`, set `ADMIN_TOKEN` and `PLAYWRIGHT_BASE_URL`, and run `npm run test:e2e` inside `frontend/`.

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
