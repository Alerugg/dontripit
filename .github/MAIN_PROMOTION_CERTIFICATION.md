# Catalog V2 → Main promotion certification

Certified on 2026-08-17 before promotion.

## Roadmap

Points 1–9 are closed and evidence-backed. The final promotion gate was added specifically to prevent a merge from activating historical production writers.

## Production scheduling contract

`daily-catalog-v2-orchestrator.yml` is the sole scheduled production writer. It dispatches, sequentially, the current ref of:

1. `refresh-onepiece-yugioh-canonical-v2.yml`
2. `sync-cardmarket-master-production-v2.yml`
3. `sync-cardmarket-website-paths.yml`
4. `sync-regional-tcg-content.yml`

Those children are `workflow_dispatch`-only. Riftbound remains excluded from automated production ingestion.

Historical production workflows such as `migrate.yml` and `ingest.yml` are manual-only. The old Pokémon-only Cardmarket cron is retired.

Scheduled workflows outside the daily production orchestrator are read-only audits/security/performance/recovery drills.

## Certification evidence

- Point 8 expanded architecture: Actions run `32071763810` — SUCCESS.
- Point 9 frontend/legal/cookies/SEO/accessibility: run `32070814800` — SUCCESS.
- Point 9 synthetic production residue: run `32070978121` — SUCCESS, zero synthetic identities and zero obvious test accounts.
- Main promotion safety inventory: run `32071815716` — SUCCESS.
  - 343 workflows inventoried.
  - zero production database writers triggered by pushes to `main`.
  - zero independently scheduled production database writers.

## Deployment observation

The repository contains only declarative Vercel configuration (`backend/vercel.json`, `frontend/vercel.json`, `landing/vercel.json`); no repository workflow deploys to Vercel on a main push. At certification time the connected Vercel frontend and API projects were receiving production deployments from the `catalog-v2` Git branch. The branch is therefore kept aligned with the promoted `main` commit immediately after merge until Vercel's production-branch setting is moved to `main` in project settings.
