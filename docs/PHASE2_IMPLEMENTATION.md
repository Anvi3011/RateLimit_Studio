# Phase 2 implementation and validation

Status: prepared locally on `deployment/dual-mode`; not committed, pushed or deployed. No cloud accounts/resources were created and no paid resources were selected. The Phase 1 audit remains unchanged. The disposable local test containers and network were removed after validation, without removing any volumes.

## Implemented result

- Public mode: one non-root supervised Render Free web container with real Flask/Gunicorn, outbox publisher and RabbitMQ consumer. It uses external TLS Neon PostgreSQL, Upstash native Redis and CloudAMQP RabbitMQ. Vercel proxies same-origin API requests without React changes. All three roles sleep/wake with Render.
- Reliability: PostgreSQL unique request IDs are authoritative; insert-or-conflict and commit precede ACK. Old Redis markers cannot hide missing logs. Infrastructure failures requeue with backoff; poison records dead-letter. Post-commit worker telemetry is atomic in Redis. Publisher confirms, connection reuse, bounded waits and a renewable singleton lease support restart recovery and overlapping deployments. A hard-killed publisher can take up to 120 seconds to reacquire its old lease.
- Health/security: database included in readiness, cheap supervisor-aware liveness, TLS/secret/origin validation, Secure/HttpOnly/Lax cookies, active CSRF checks, uncached API responses, no unverified public forwarding-header trust, generic database errors and disabled public global cleanup.
- EC2: isolated HTTP/TLS overrides preserve all eight named services and all named volumes. TLS configures existing nginx. Rollback derives the existing Compose project name, including `.env` project settings. LF shell line endings are enforced for Linux.
- Local mode: base Compose, algorithms, React source, CSS and lockfile remain unchanged. Local `.env` was not modified. No reset, truncate, drop, replay against existing data, volume removal or pruning was performed.

## Passed validation

| Check | Result |
| --- | --- |
| Isolated backend/worker/deployment pytest | **72 passed**, final run 23.67 seconds; SQLite/fake Redis only in unit tests |
| Actual combined Docker image build | Passed, Python 3.12, non-root runtime |
| Real Redis/RabbitMQ/PostgreSQL integration | Passed before and after hard-kill recovery; no mock processing |
| Both actual Lua algorithms | Accepted and rate-rejected HTTP responses verified |
| SQL persistence/analytics/session isolation | Passed, matching SQL history, metrics, charts and isolated visitors |
| Delivery/idempotency | Broker redelivery, old-marker repair and six concurrent SQL deliveries produce one row per unique request ID |
| Transaction-local SQL timeouts | Both PostgreSQL modules verified at 10 seconds, avoiding unsupported Neon pooler startup options |
| Real process crash recovery | Worker and publisher replaced after SIGKILL; new publisher lease acquired and all-dependency readiness recovered |
| Existing Playwright browser scenario | **1 passed**, 11.4 seconds, through localhost test frontend/proxy |
| Unchanged Vite production build | Passed on host and in Node 22 Docker frontend build |
| Render and Vercel provider schemas | Passed document validation against official schemas |
| EC2 HTTP and TLS Compose configuration | Both pass `config --quiet` with dummy environment values |
| AWS Linux shell syntax | All shell scripts pass `bash -n` |
| Source invariants | React JSX, CSS, both Lua algorithm scripts and base Compose match Git HEAD; `git diff --check` passes |

The combined container measured approximately **175 MiB** in the local test environment. This is a snapshot, not a guarantee under remote load or proof of Render capacity.

## Failures encountered and resolved

- Windows sandbox prevented Vite subprocess spawning (`EPERM`); an authorized elevated build passed without source changes.
- Initial manifest unit test found unquoted YAML `off` parsed as boolean; quoting the field fixed it.
- Initial browser run rejected localhost mutations because its test URL was absent from the test allowlist; corrected only the isolated test environment and the unchanged test passed.
- Windows checkout CRLF endings caused Linux Bash syntax failure; normalized shell files and added `.gitattributes` LF enforcement, then all scripts passed.
- Crash-test review found that retrying a held lease by repeatedly exiting could hit the supervisor failure limit. Publisher now waits in the process for lease expiry; the stronger real restart check passed.
- Generic jsonschema meta-schema validation rejected a custom construct in Vercel's published schema. Direct document validation using the corresponding validator passed; Render's schema also passed.

No known failing automated checks remain. This does not imply cloud validation: provider-specific TLS, exact Lua limits/permissions, browser Set-Cookie forwarding, quota usage, actual Render suspension/wake and EC2 Lab networking/TLS are pending deployment approval and manual smoke tests. No cloud deployment has occurred.

## Remaining limits

Free Render sleeps and has constrained CPU/memory/hours and external-traffic suspension thresholds; cold starts and publisher lease recovery delay processing. Redis/Neon/RabbitMQ free quotas and queue lifecycle policies apply. Unchanged UI polling and synthetic traffic can consume significant Redis commands/Neon compute. Traffic generation/benchmarks remain synchronous and should use small batches within upstream timeouts. Accurate public client IP requires separately verified proxy trust; default header identity works without trusting incoming forwarding headers. Redis telemetry after its seven-day marker expiry can count a replay again; PostgreSQL history remains unique. An API request that cannot enqueue due to Redis outage/outbox overflow is still not guaranteed a SQL log; accepted-queue commit/ACK gaps are repaired, not replaced with a false exactly-once guarantee across all stores. Cleanup/backup/restore and replay remain explicit operator procedures.

## Manual steps and approval

Follow [PUBLIC_DEPLOYMENT.md](PUBLIC_DEPLOYMENT.md) for the ordered free-account steps and [EC2 instructions](../deployment/aws/DUAL_MODE.md) for Lab preparation. After approval, confirm all free tiers and a no-payment-method Render workspace to avoid account-level overages, create independent Neon/Upstash/CloudAMQP resources, enter private connection URLs and a fresh session secret into Render, set the exact Vercel origin, replace the non-secret Render hostname placeholder in `frontend/vercel.json`, and configure Vercel's root as `frontend`. Do not expose credentials through Vite variables. A Git-connected deployment also requires separately authorized publication of these currently local changes. Do not deploy an old remote branch by mistake.

Deployment is the next approval gate. No push, cloud creation or deployment is authorized by this report.

## Exact Phase 2 file inventory

Modified tracked files:

- `.env.example`
- `README.md`
- `backend/app/__init__.py`
- `backend/app/config/__init__.py`
- `backend/app/db/connection.py`
- `backend/app/db/service.py`
- `backend/app/redis/__init__.py`
- `backend/app/routes/api.py`
- `deployment/aws/README.md`
- `deployment/aws/backup.sh`
- `deployment/aws/deploy.sh`
- `deployment/aws/healthcheck.sh`
- `deployment/aws/install-docker.sh`
- `deployment/aws/rollback.sh`
- `deployment/aws/update.sh`
- `docs/ARCHITECTURE.md`
- `docs/AWS_DEPLOYMENT.md`
- `docs/KNOWN_LIMITATIONS.md`
- `docs/TESTING.md`
- `frontend/nginx.conf`
- `worker/consumer.py`
- `worker/db.py`
- `worker/processor.py`
- `worker/publisher.py`
- `worker/tests/test_processor.py`
- `worker/tests/test_publisher.py`

New files:

- `.dockerignore`
- `.env.public.example`
- `.gitattributes`
- `backend/tests/test_deployment.py`
- `deployment/aws/DUAL_MODE.md`
- `deployment/aws/compose.ec2.yml`
- `deployment/aws/compose.tls.yml`
- `deployment/public/Dockerfile`
- `deployment/public/supervisor.py`
- `deployment/tests/compose.integration.yml`
- `deployment/tests/integration.py`
- `deployment/tests/requirements.txt`
- `deployment/tests/restart_check.py`
- `deployment/tests/test_manifests.py`
- `deployment/tests/test_supervisor.py`
- `docs/PHASE2_IMPLEMENTATION.md`
- `docs/PUBLIC_DEPLOYMENT.md`
- `frontend/vercel.json`
- `render.yaml`
- `worker/tests/test_consumer.py`

`docs/DEPLOYMENT_AUDIT.md` was already present from Phase 1 and was not edited during Phase 2. Generated build/dependency/test-output files are ignored and are not deployment source changes.
