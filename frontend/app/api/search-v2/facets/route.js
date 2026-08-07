import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = (searchParams.get('game') || '').trim().toLowerCase()
  if (!game) {
    return NextResponse.json({ error: 'game_required' }, { status: 400 })
  }

  const upstream = await callInternalApi(`/api/v2/games/${encodeURIComponent(game)}/facets`)
  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'search_v2_facets_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { game, facets: [], groups: {} })
}
