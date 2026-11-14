# AWS Learner Lab deployment preparation

This is a single EC2 Docker Compose deployment for an academic demonstration. It is not a production high-availability service. No AWS resources have been created. See `KNOWN_LIMITATIONS.md` before inviting users.

## Preflight status (2026-11-05)

- AWS CLI: not installed on the current Windows host.
- `aws sts get-caller-identity`: not run because the CLI is absent. AWS authentication is therefore unverified; no credentials were read or displayed.
- Region: `AWS_REGION` and `AWS_DEFAULT_REGION` are unset; `%USERPROFILE%\.aws\config` is absent. No region is configured.
- `AWS_SESSION_TOKEN` is not present in this shell. Temporary Learner Lab credentials: cannot be determined until lab credentials are configured locally. After configuring, check whether an `AWS_SESSION_TOKEN` is present without printing its value and inspect only the non-secret caller ARN/account from STS. Learner Lab credentials are temporary and expire when the lab session ends; refresh them from the Learner Lab page. Do not paste them in chat or into the project.
- Permitted EC2 instance types/regions/quotas: unknown until authenticated; Learner Lab policies may restrict them. Check the Learner Lab console and `aws ec2 describe-instance-type-offerings` in the selected region before launch.
- Local Linux Docker Engine: `linux/x86_64`. Backend, worker, publisher, frontend, and Locust images built. Compose config validation passed; current local services report healthy. The optional Playwright container image was not finished because its 786 MB browser base downloaded too slowly. This is not an EC2/AWS Linux deployment verification.

For AWS CLI on Windows, install the official AWS CLI v2 MSI from the [AWS CLI installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html). In Learner Lab, start the lab, open **AWS Details**, and use its provided AWS CLI credentials workflow. Keep the access key, secret, and session token in the local shell/profile or credentials file with restrictive permissions; never commit them or put them in `.env`. Set the region shown by Learner Lab, then verify locally:

```powershell
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
aws configure get region
```

An assumed-role ARN and a configured session token are consistent with temporary credentials, but the ARN alone does not prove a Learner Lab session. Do not use `aws configure` with credentials on a shared machine unless its local credential-file protections are understood. Official docs: [temporary credentials](https://docs.aws.amazon.com/cli/latest/userguide/cli-authentication-short-term.html) and [CLI config files](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html).

## Architecture and public ports

Compose publishes only Nginx: the default `nginx` service maps host `HTTP_PORT` to container port 80; TLS profile `nginx-tls` maps host 80/443. Redis, RabbitMQ, publisher, worker, Flask, and frontend have no host port mappings. They communicate on Compose's private `internal` network. Redis and RabbitMQ data use named Docker volumes `redis_data` and `rabbitmq_data`; deployment/update commands do not remove them. Restart policies are `unless-stopped`; Redis, RabbitMQ, backend, and worker have health checks. Public requests flow through Nginx -> frontend Nginx -> Flask.

Nginx is the only public application entry point. On a single instance public IP, users browse `http://PUBLIC_IP/` for an academic HTTP demo, or `https://DOMAIN/` when using a trusted domain certificate. Users do not need local Python, Node, Docker, Redis, or RabbitMQ. The EC2 instance and its Docker containers must remain running for remote access. A stopped/terminated instance or expired public address breaks the URL.

Application configuration writes, fault changes, benchmark runs, traffic generation, and logs analytics are completely public and isolated to each visitor's demo session. No admin token or credentials are required for demonstration features. Visitors cannot access infrastructure commands, containers, or internal databases. Keep fault injection disabled in production configurations.

## Security group

Create a security group with inbound rules:

| Port | Source | Use |
|---|---|---|
| TCP 22 | `YOUR_PUBLIC_IP/32` only | SSH administration |
| TCP 80 | `0.0.0.0/0` (and `::/0` only if IPv6 is configured) | Optional HTTP demo or ACME HTTP validation and HTTPS redirect |
| TCP 443 | `0.0.0.0/0` (and `::/0` only if IPv6 is configured) | HTTPS dashboard |

Never add ingress for 5000, 6379, 5672, or 15672. Remove port 80 only if the chosen certificate/redirect plan does not need it. Do not configure an unrestricted SSH rule. Host firewall rules, if enabled, must match this policy.

## Host sizing and costs

For an academic demo, start by checking whether Learner Lab permits an Ubuntu 22.04/24.04 x86_64 `t3.medium` (2 vCPU, 4 GiB RAM); this is a starting estimate, not a load-tested guarantee. Use a modest 20–30 GiB gp3 root disk for OS, Docker images, logs, and small local datasets. Monitor memory, disk, queue depth, and CPU; constrain logs and prune old unused images only after checking current deployment state. Choose a cheaper/smaller instance only if the lab requires it and accept slower builds and lower concurrent capacity.

No reliable dollar estimate can be given without the selected region, instance, hours, storage, public IPv4, and current Learner Lab credit policy. Check the [AWS Pricing Calculator](https://calculator.aws/) and Learner Lab balance/expiry before launch. A running instance, EBS storage, snapshots, and public IPv4 may incur charges or consume lab credits; AWS currently bills public IPv4 usage in applicable cases. Stop the instance when unused; stopping usually leaves EBS charges and may change a non-elastic public IPv4. Avoid Elastic IP, RDS, ElastiCache, Amazon MQ, ECS, and EKS for this initial demonstration. Lab credentials expire; the instance may keep running and incur charges after local credentials expire.

## Prepare the EC2 host

1. In Learner Lab, confirm the lab is active, region/quota/instance type are permitted, and remaining credits/time are sufficient. Create no resources until explicitly approved.
2. After an approved launch, SSH only from your own `/32` source. Once the GitHub repository is published, clone it to the host (replace the repository URL): `git clone https://github.com/OWNER/REPOSITORY.git /opt/ratelimit-studio && cd /opt/ratelimit-studio`. Do not copy AWS credentials to the EC2 host; this app needs none.
3. For Ubuntu 22.04/24.04, install Docker Engine and Compose using the included script:

   ```sh
   sudo bash deployment/aws/install-docker.sh
   ```

   It uses Docker's official Ubuntu package repository. Other OS images must follow the vendor and Docker installation instructions; the script deliberately rejects unsupported distributions.
4. Create `.env` only if it does not already exist: `cp .env.example .env`; set `POSTGRES_PASSWORD` to a secure password, `RABBITMQ_PASSWORD` to at least 64 hexadecimal characters, `RABBITMQ_USER` to a unique user, `POSTGRES_USER=ratelimit_user`, `POSTGRES_DB=ratelimit_studio`, `FAULTS_ENABLED=false`, `RATE_LIMIT_FAIL_MODE=closed`, and `HTTP_PORT=80`. Set `FRONTEND_ORIGIN` to `http://EC2_PUBLIC_IP` for the HTTP demo or `https://your.domain` for TLS. The deploy script sets mode 0600 and refuses placeholders, weak password values, loopback origins, or the default 8080 port. Keep `.env` private and out of backups shared in public locations.
5. For public-IP HTTP demonstration, run `bash deployment/aws/deploy.sh http`. For trusted domain HTTPS, first point DNS at the instance and issue/install a real certificate as below, then run `HEALTHCHECK_URL=https://your.domain/api/health bash deployment/aws/deploy.sh tls`. The TLS deploy requires that HTTPS health URL and matching valid files. `TLS_CERT_DIR` defaults to `./deployment/tls`; the rollback script mounts the current certificate directory directly. Never use the self-signed smoke-test certificate for public users.
6. Check `docker compose ps`, `bash deployment/aws/healthcheck.sh`, and `docker compose logs --tail=100 backend publisher worker nginx` (or `nginx-tls`). Visit the public URL from another laptop/network after the security group and host firewall are correct.

The deploy script builds every selected image locally on EC2. Allow for memory, disk, and time during the frontend build. No image-pull preflight or AWS-host build has been performed from this environment.

## HTTPS and domain ownership

An EC2 public IPv4 address over HTTP is suitable only for a temporary, non-sensitive lab demo: traffic is unencrypted and browsers show no trusted lock. It does not require domain ownership. A self-signed certificate encrypts a connection but is not trusted by ordinary browsers and causes certificate warnings; it is not publicly trusted HTTPS.

Trusted HTTPS requires a domain you own, DNS pointing to the instance, and a trusted CA certificate for that hostname. No domain ownership is assumed. The included Nginx TLS profile passed a local configuration/smoke test with a temporary self-signed certificate, but public certificate issuance, installation, renewal and failure recovery have not been verified. For a domain, after DNS points to this host and port 80 is allowed, a standalone Certbot first issue can be done while the edge container is stopped:

```sh
sudo apt-get update && sudo apt-get install -y certbot
docker compose --profile tls stop nginx nginx-tls || true
sudo certbot certonly --standalone -d your.domain.example
sudo install -d -m 0750 deployment/tls
sudo install -m 0644 /etc/letsencrypt/live/your.domain.example/fullchain.pem deployment/tls/fullchain.pem
sudo install -m 0600 /etc/letsencrypt/live/your.domain.example/privkey.pem deployment/tls/privkey.pem
HEALTHCHECK_URL=https://your.domain.example/api/health bash deployment/aws/deploy.sh tls
```

Replace every example domain and use a domain you control. For renewal, arrange a tested pre-hook to stop the TLS container, a deploy hook to copy the renewed chain/key and restart/recreate `nginx-tls`, and run `certbot renew --dry-run`; this lifecycle has not been tested here. An ephemeral public IP can change after stopping/starting an instance, so update DNS if needed. AWS public IPv4 charges may apply; check [current VPC pricing](https://aws.amazon.com/vpc/pricing/). If using a managed TLS proxy instead, restrict instance ingress to that proxy and re-evaluate the trusted proxy chain; this project does not configure a managed load balancer.

## Update, backup, restore, rollback

- Update code from a reviewed source revision, then run `bash deployment/aws/update.sh http` or `bash deployment/aws/update.sh tls`. This rebuilds images and reuses named data volumes. Review logs and run the health check after each update.
- Make a consistent local archive with `sudo bash deployment/aws/backup.sh [destination-directory]`. The script briefly stops backend, publisher, worker, RabbitMQ, Redis, and PostgreSQL; archives all three named volumes (`postgres_data`, `redis_data`, `rabbitmq_data`) and `.env`; then restarts services. Store the archive with strict access and encryption. It contains credentials. Copy an encrypted backup off-instance and periodically test restoring into a separate, isolated EC2 host/project name. Never restore into the current project/volumes while preserving their data is required.
- Restore into a fresh isolated host/project only. With the archive copied privately to that host, set a unique Compose project name so it creates new named volumes, restore `.env` securely, then:

```sh
export COMPOSE_PROJECT_NAME=ratelimit-restore-test
install -m 0600 /path/to/archive/env.backup .env
docker compose create postgres redis rabbitmq
ARCHIVE_DIR=/path/to/archive
docker compose run --rm --no-deps -v "$ARCHIVE_DIR:/backup:ro" --entrypoint sh postgres -c 'tar -xzf /backup/postgres_data.tgz -C /var/lib/postgresql/data'
docker compose run --rm --no-deps -v "$ARCHIVE_DIR:/backup:ro" --entrypoint sh redis -c 'tar -xzf /backup/redis_data.tgz -C /data'
docker compose run --rm --no-deps -v "$ARCHIVE_DIR:/backup:ro" --entrypoint sh rabbitmq -c 'tar -xzf /backup/rabbitmq_data.tgz -C /var/lib/rabbitmq'
bash deployment/aws/deploy.sh http
```

Run these commands only on the separate restore host, with the same Compose project files and after checking archive integrity. Change to TLS mode only after certificate setup. Never use `docker compose down -v`, `docker volume rm`, or `FLUSHDB` on the existing project. A restore has not been executed or verified in this environment.
- After the source is in Git, roll back application code with `bash deployment/aws/rollback.sh <known-good-commit-or-tag> [http|tls]`. The script creates a persistent ignored release worktree, requires the existing `.env`, reuses the Compose project name (set `COMPOSE_PROJECT_NAME` if the original deployment used a custom project name), and does not remove volumes. TLS rollback uses the current certificate files. Review service health after rollback. Do not restore old data as a code rollback. If a future schema/data migration is introduced, first establish backward compatibility and a separate tested data rollback procedure. The script has passed syntax validation but has not been exercised against an EC2 deployment.

## Exact provisioning commands (not run)

These are a reviewed command outline only. Substitute values after checking the Learner Lab console and region; do not run until the user explicitly approves resource creation. `AMI_ID` must be a current Ubuntu 22.04/24.04 image ID for the region, `KEY_NAME` an existing protected key pair, and `SUBNET_ID` a subnet with a route to an Internet Gateway and public IPv4 assignment. `YOUR_PUBLIC_IP` must be the operator current public address with `/32`. First discover a VPC/subnet and create the security group with the rules above. These commands create cloud resources:

```sh
REGION=REPLACE_WITH_LAB_REGION
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SUBNET_ID=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true --query 'Subnets[0].SubnetId' --output text)
AMI_ID=$(aws ssm get-parameter --region "$REGION" --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
aws ec2 describe-instance-type-offerings --region "$REGION" --location-type region --filters Name=instance-type,Values=t3.medium --query 'InstanceTypeOfferings[].InstanceType' --output text
SECURITY_GROUP_ID=$(aws ec2 create-security-group --region "$REGION" --group-name ratelimit-studio-demo --description 'RateLimit Studio web demo' --vpc-id "$VPC_ID" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SECURITY_GROUP_ID" --ip-permissions '[{"IpProtocol":"tcp","FromPort":22,"ToPort":22,"IpRanges":[{"CidrIp":"YOUR_PUBLIC_IP/32","Description":"Operator SSH only"}]},{"IpProtocol":"tcp","FromPort":80,"ToPort":80,"IpRanges":[{"CidrIp":"0.0.0.0/0","Description":"HTTP or ACME redirect"}]},{"IpProtocol":"tcp","FromPort":443,"ToPort":443,"IpRanges":[{"CidrIp":"0.0.0.0/0","Description":"HTTPS dashboard"}]}]'
aws ec2 run-instances --region "$REGION" --image-id "$AMI_ID" --instance-type t3.medium --key-name "$KEY_NAME" --security-group-ids "$SECURITY_GROUP_ID" --subnet-id "$SUBNET_ID" --associate-public-ip-address --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":25,"VolumeType":"gp3","DeleteOnTermination":true}}]' --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=ratelimit-studio-demo}]' --count 1
```

A launch is not fully specified until the subnet, SSH key, security group, AMI, selected region, Learner Lab limits, and remaining credit/time are confirmed. The command above was not executed. No Elastic IP is allocated.

## Verification record and blockers

- AWS CLI/auth/region/type offerings: unavailable as described in preflight. No AWS credentials were read or printed.
- `docker compose config --quiet`: passed. App images for backend, worker, publisher and frontend built using local Linux Docker Engine `linux/x86_64`; Locust benchmark image also built. The optional Playwright test container build was stopped while fetching its 786 MB browser image; the host-Chrome browser flow had passed in prior verification.
- Nginx verification: `nginx -t` passed; a request to `/` returned the React root page, and `/api/health` proxied through the gateway/frontend to Flask and returned healthy. Compose port mapping shows only the Nginx service publishes in HTTP mode; TLS mode publishes through `nginx-tls` on 80/443. No Redis or RabbitMQ host ports are configured.
- Local runtime after relay correction: Redis, RabbitMQ, backend, worker, frontend and Nginx were healthy; publisher heartbeat was healthy, RabbitMQ queue depth was 0, and the Redis outbox drained from 4,478 to 0. The pre-existing failed-event list still contains 28 entries and was preserved for review; it was not deleted or replayed. The publish-confirm defect and correction are recorded in `KNOWN_LIMITATIONS.md`.
- Backend Pytest: 20 passed in the prior hardening run. Worker/publisher Pytest: 7 passed after adding the Pika confirm regression test. The deployment shell scripts passed Bash syntax checks inside the local Linux container.
- No EC2/Linux host launch test, AWS networking, public access, real domain certificate, renewal, or restore drill was executed. No AWS resource was created and no cloud cost was incurred. Deployment scripts have not run on EC2.
