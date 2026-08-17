import { NextResponse } from 'next/server'
import { normalizeGameSlug } from '../../../../lib/catalog/games'
import { getReleaseRegions, getVerifiedReleases } from '../../../../lib/news/releases'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = normalizeGameSlug((searchParams.get('game') || '').trim().toLowerCase())
  const region = (searchParams.get('region') || '').trim().toUpperCase()
  const upcoming = searchParams.get('upcoming') !== '0'
  const limit = Math.min(50, Math.max(1, Number(searchParams.get('limit') || 12)))

  const items = getVerifiedReleases({ game, region, upcoming, limit })

  return NextResponse.json({
    items,
    regions: getReleaseRegions(game),
    provider: 'official_verified_calendar',
    provenance: 'official-source-only',
  })
}
