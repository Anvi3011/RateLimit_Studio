# GitHub publishing checklist

Publishing preparation status (2026-11-05): the GitHub repository `https://github.com/Anvi3011/Rate_Limiter.git` was checked read-only and returned no branches or tags. The local `main` branch and `origin` remote are configured, and a staged review is prepared. No commit or push has been created. Explicit approval is still required before either action.

## Before creating the first commit

1. Review `.gitignore`. It excludes `.env` and `.env.*` while explicitly allowing `.env.example`, AWS/SSH credentials, private keys and certificates, local environments, browser artifacts, benchmark outputs, backups, and rollback worktrees.
2. Keep `.env` out of Git. `.env.example` contains placeholders only. Never add `AWS-Keys/`, `deployment/tls/fullchain.pem`, `privkey.pem`, AWS credentials, session tokens, `.aws/`, or backup archives.
3. Run a redacted scanner locally, for example `gitleaks dir --redact --no-banner .`. Review findings locally; do not paste secret-bearing output in issues, pull requests, or chat. Enable GitHub secret scanning/push protection if available for the repository.
4. Confirm the source and tests are included: `backend/`, `worker/`, `frontend/src/`, frontend tests, benchmarks source, `deployment/`, and docs. Generated `node_modules/`, `.venv/`, `dist/`, local test reports, benchmark output CSV/JSON, and backups are intentionally excluded.
5. After the local initialization below, check the candidate list before commit. These commands display filenames and status, not file contents:

   ```powershell
   git check-ignore -v .env .env.local deployment/tls/fullchain.pem deployment/tls/privkey.pem
   git status --short --ignored
   git diff --cached --name-only
   ```

   `.env.example` should not be ignored. If a credential was ever committed in repository history, removing the file from the latest tree is insufficient; stop and rotate it, then clean the history before publishing.

## Local initialization and staged review

From `C:\RateLimiting` in PowerShell or Git Bash, the local staging review can be prepared before publishing approval:

```powershell
git init -b main
git remote add origin https://github.com/Anvi3011/Rate_Limiter.git
git add -A
git status --short
git diff --cached --name-only
```

Review the staged paths. If `.env`, a key/certificate, credential file, backup, `.venv/`, `node_modules/`, or generated report appears, unstage it and correct `.gitignore` before committing. Do not use `git add -f` for secrets or generated artifacts. The current directory has no `.git` history, so this starts a clean history; verify again if that changes before publishing.

Only after explicit approval to publish, create the commit and push:

```powershell
git commit -m "Prepare RateLimit Studio for deployment"
git remote -v
git push -u origin main
```

The final command publishes source code and must not be run until explicit approval. Use GitHub's browser login or `gh auth login`; never put a personal access token in the remote URL, source, `.env`, or command history.

## Deploy from GitHub to EC2 (after AWS approval)

Once the repository is published, clone it to the EC2 host and follow [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md). The repository contains source and sanitized templates only; create the server `.env` on the instance, set strict file permissions, and install a trusted TLS certificate separately. GitHub deploy keys/tokens and AWS Learner Lab credentials are not needed by the application containers.
