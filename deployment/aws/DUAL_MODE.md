# EC2 eight-service mode

Prepared for AWS Academy Learner's Lab; not deployed by Phase 2. Use existing credits and inspect allowed instance sizes/regions in your Lab before selecting anything. No AWS resources, IAM policies or security groups are created by this repository.

Work from the repository on `deployment/dual-mode`, not its parent checkout. Keep the **existing Compose project name** and `.env`; do not change named volume identities. Record them before updating an existing instance. Set a private unique `SESSION_SECRET` of at least 32 characters, PostgreSQL and URL-safe RabbitMQ credentials, `FAULTS_ENABLED=false`, `HTTP_PORT=80`, and the exact public `FRONTEND_ORIGIN`. Do not overwrite an existing database password merely to match a new template.

After approval, `deployment/aws/deploy.sh http` combines the base Compose file with `compose.ec2.yml`. TLS additionally applies `compose.tls.yml` to the existing `nginx` service. All eight defaults remain: postgres, rabbitmq, redis, worker, publisher, backend, frontend, nginx. The optional old nginx-tls service is stopped, not substituted for nginx. No volume is removed. Default local Compose remains unchanged.

For TLS, supply a trusted `fullchain.pem`/`privkey.pem` in the existing protected certificate directory, set HTTPS origins and `HEALTHCHECK_URL=https://YOUR_DOMAIN/api/health`. The existing `nginx` service publishes 443 as well as its configured HTTP port. The frontend proxy waits up to 120 seconds for legitimate slow API requests and fault simulations; use small batches within Gunicorn's existing local timeout.

Only expose the edge ports 80/443; restrict SSH to your IP. Keep PostgreSQL, Redis, AMQP and Flask ports private. Lab shutdown stops the demo and can change the public IP; the independent public demo does not use this instance.

Updates and rollbacks retain the Compose project and named volumes. The rollback script refuses targets that predate these overrides; use a separately reviewed procedure for an older revision rather than silently deploying a different topology. No schema changes in this phase drop or alter tables. Backup/restore and retention cleanup are separate operations requiring review; do not run a reset or a volume-pruning command.

Before approval, safe configuration validation is:

```sh
docker compose -f docker-compose.yml -f deployment/aws/compose.ec2.yml config --quiet
docker compose -f docker-compose.yml -f deployment/aws/compose.ec2.yml -f deployment/aws/compose.tls.yml config --quiet
```

These commands require locally supplied environment values but do not start services. Avoid printing the fully interpolated configuration because it contains credentials.
