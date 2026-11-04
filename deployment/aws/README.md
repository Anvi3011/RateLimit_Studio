# AWS deployment scripts

These scripts prepare and operate the existing Docker Compose project on an Ubuntu 22.04/24.04 EC2 host. They do not call AWS APIs or create cloud resources.

- `install-docker.sh`: installs Docker Engine and Compose v2 from Docker's official Ubuntu repository.
- `deploy.sh [http|tls]`: validates production secrets, sets `.env` to mode 0600, builds and starts the app, then checks health. HTTP deployment requires `HTTP_PORT=80`; TLS deployment requires real certificate files.
- `update.sh [http|tls]`: rebuilds images, redeploys, and health-checks.
- `healthcheck.sh`: retries the Nginx health endpoint and prints service diagnostics on failure.
- `rollback.sh <known-good-commit-or-tag> [http|tls]`: builds a persistent release worktree from Git history and redeploys it using the existing Compose project name and named volumes. TLS rollback uses the current certificate directory. Test only after a repository and known-good revision exist.
- `backup.sh [directory]`: briefly stops writers and brokers, archives Redis and RabbitMQ named volumes and `.env`, restarts services, and writes a private backup directory. Keep the archive encrypted and access-controlled. Restore only into a separate fresh project/host unless you are intentionally replacing its data.

Follow [AWS_DEPLOYMENT.md](../../docs/AWS_DEPLOYMENT.md) for security groups, region/Learner Lab checks, TLS, backup/restore, rollback, and the unexecuted provisioning outline. No AWS resources or local Redis data were changed to prepare these files.
