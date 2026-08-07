import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = (searchParams.get('game') || '').trim().toLowerCase()
  const key = (searchParams.get('key') || '').trim().toLowerCase()
  if (!game || !key) {
    return NextResponse.json({ error: 'game_and_key_required' }, { status: 400 })
  }

  const upstream = await callInternalApi(
    `/api/v2/games/${encodeURIComponent(game)}/facets/${encodeURIComponent(key)}/values`,
    {
      params: {
        q: searchParams.get('q') || '',
        limit: searchParams.get('limit') || 30,
      },
    },
  )

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: upstream.payload?.error || 'search_v2_facet_values_failed',
        message: upstream.payload?.detail || getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { game, facet: key, items: [] })
}
