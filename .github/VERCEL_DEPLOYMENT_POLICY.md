# Vercel deployment policy

This repository is a monorepo with separate Vercel projects.

- `dontripit` builds from `frontend/` and must only build when files under `frontend/` change.
- `dontripit-api` builds from `backend/` and must only build when files under `backend/` change.
- The legacy/root Vercel project must not auto-deploy from Git.

The path guards are defined in each project's `vercel.json`. The repository-root `vercel.json` disables automatic Git deployments for the legacy/root project.

This policy exists to prevent backend/CI-only commits from rebuilding unrelated Vercel projects and consuming unnecessary Build CPU Minutes.
