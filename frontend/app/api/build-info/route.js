import { NextResponse } from 'next/server'

function first(...values) {
  return values.find((value) => typeof value === 'string' && value.trim())?.trim() || null
}

export async function GET() {
  const railway = Boolean(process.env.RAILWAY_ENVIRONMENT || process.env.RAILWAY_GIT_COMMIT_SHA)
  const vercel = Boolean(process.env.VERCEL || process.env.VERCEL_GIT_COMMIT_SHA)

  return NextResponse.json(
    {
      service: 'frontend',
      platform: railway ? 'railway' : vercel ? 'vercel' : 'unknown',
      commit_sha: first(
        process.env.RAILWAY_GIT_COMMIT_SHA,
        process.env.VERCEL_GIT_COMMIT_SHA,
        process.env.GITHUB_SHA,
      ),
      branch: first(
        process.env.RAILWAY_GIT_BRANCH,
        process.env.VERCEL_GIT_COMMIT_REF,
        process.env.GITHUB_REF_NAME,
      ),
      environment: first(
        process.env.RAILWAY_ENVIRONMENT_NAME,
        process.env.RAILWAY_ENVIRONMENT,
        process.env.VERCEL_ENV,
      ),
      build_contract: 'catalog-v2-search-v2',
    },
    {
      headers: {
        'Cache-Control': 'no-store',
      },
    },
  )
}
