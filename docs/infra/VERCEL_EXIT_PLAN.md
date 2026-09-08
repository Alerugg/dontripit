# Don’tRipIt — Vercel exit plan

## Goal

Move production hosting off Vercel while keeping Neon unchanged.

Target architecture:

- Frontend: Cloudflare Workers (migration handled separately)
- Backend API: Google Cloud Run
- Database: Neon PostgreSQL (unchanged)
- CI/CD: GitHub Actions
- Production domains: `dontripit.com`, `www.dontripit.com`, `api.dontripit.com`

## Safety rules

1. Do not delete the Vercel team while the billing/refund dispute is open.
2. Do not move `api.dontripit.com` until the Cloud Run URL passes production probes.
3. Cloud Run deployment remains manual (`workflow_dispatch`) until cutover is certified.
4. Only backend/infra changes belong in this migration branch. No frontend functional changes.
5. Neon remains the source of truth; no database migration is performed.

## Repository preparation

Branch: `infra/exit-vercel-cloud-run`

Prepared files:

- `backend/Dockerfile` — production Gunicorn container for Cloud Run
- `backend/.dockerignore` — reduced Docker build context
- `.github/workflows/deploy-backend-cloud-run.yml` — manual GitHub Actions deploy
- `infra/gcp/bootstrap-cloud-run.sh` — repeatable GCP/WIF bootstrap
- `backend/app/routes/health.py` — portable Vercel/Cloud Run runtime metadata

## Google Cloud target defaults

- Region: `europe-southwest1` (Madrid)
- Cloud Run service: `dontripit-api`
- Artifact Registry repository: `dontripit`
- Min instances: `0`
- Max instances: `2`
- CPU: `1`
- Memory: `512Mi`
- Concurrency: `20`
- Request timeout: `120s`
- Public ingress: enabled; API product/auth middleware remains application-controlled

## One-time Google Cloud bootstrap

Run in Google Cloud Shell after creating/selecting a project with billing enabled:

```bash
export PROJECT_ID="YOUR_GCP_PROJECT_ID"
curl -fsSL https://raw.githubusercontent.com/Alerugg/dontripit/infra/exit-vercel-cloud-run/infra/gcp/bootstrap-cloud-run.sh -o /tmp/bootstrap-cloud-run.sh
bash /tmp/bootstrap-cloud-run.sh
```

The script prints the exact GitHub repository variables to create.

## GitHub Actions repository variables

Create under:

`GitHub -> Alerugg/dontripit -> Settings -> Secrets and variables -> Actions -> Variables`

Required variables:

- `GCP_PROJECT_ID`
- `GCP_REGION` (expected: `europe-southwest1`)
- `GCP_ARTIFACT_REPOSITORY` (expected: `dontripit`)
- `GCP_CLOUD_RUN_SERVICE` (expected: `dontripit-api`)
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `GCP_RUNTIME_SERVICE_ACCOUNT`

## GitHub Actions repository secrets

Create under:

`GitHub -> Alerugg/dontripit -> Settings -> Secrets and variables -> Actions -> Secrets`

Required for API parity:

- `NEON_DATABASE_URL` — production Neon pooled PostgreSQL URL
- `INTERNAL_API_KEY` — same value used by the production frontend/backend contract
- `ADMIN_CONSOLE_USERNAME`
- `ADMIN_CONSOLE_PASSWORD`

Required to preserve password recovery if configured in production:

- `RESEND_API_KEY`
- `AUTH_EMAIL_FROM`

Do not copy Vercel system variables such as `VERCEL`, `VERCEL_URL`, or `VERCEL_GIT_COMMIT_SHA`.

Other ingest-specific variables can remain in GitHub Actions workflows and do not need to live in the web API unless a runtime endpoint actually uses them.

## First deployment

The workflow is intentionally manual.

1. Open GitHub Actions.
2. Select `Deploy backend to Cloud Run`.
3. Choose branch `infra/exit-vercel-cloud-run`.
4. Run workflow.
5. The workflow authenticates with Workload Identity Federation, builds on the GitHub-hosted runner, pushes one image to Artifact Registry, deploys a Cloud Run revision, and probes `/api/health`.

Expected health response:

```json
{
  "ok": true,
  "revision": "...",
  "runtime": "cloud_run"
}
```

## Pre-cutover certification

Before changing DNS, run against the temporary `*.run.app` URL:

- `/api/health`
- exact search: `P-150`
- exact search: `OP05-119`
- exact search: `LOB-001`
- name search: `Luffy`
- name search: `Pikachu`
- auth/login flow
- collection/library read and write
- password recovery delivery
- Cardmarket links/prices
- pagination
- representative image/media endpoints

No DNS change until these pass.

## API cutover

After certification:

1. Map `api.dontripit.com` to the new Cloud Run endpoint using the chosen DNS/proxy path.
2. Keep the old Vercel API project available temporarily as rollback, but disconnect automatic Git deployments.
3. Re-run the probes through `https://api.dontripit.com`.
4. Observe errors/latency before removing the Vercel backend project.

## Vercel retirement order

Only after backend and frontend have both moved:

1. Disconnect Git auto-deploys for `dontripit-51kr`.
2. Disconnect Git auto-deploys for `dontripit-api`.
3. Disconnect Git auto-deploys for `dontripit`.
4. Remove custom domains from Vercel only after DNS is serving the replacement platforms.
5. Delete the stale `dontripit-51kr` project.
6. Delete old API/frontend projects after rollback window.
7. Keep account/team access until the Vercel refund/bank dispute is fully resolved.

## Cost controls

- Vercel plan: Hobby/downgraded.
- Cloud Run: min instances `0`, max instances `2`.
- Docker builds run on GitHub Actions, not Vercel/Cloud Build.
- Artifact Registry stores only deployment images; periodically delete obsolete revisions/images.
- Set a Google Cloud billing budget/alert immediately after project creation.
- Neon remains the only intended recurring paid service at current traffic levels.
