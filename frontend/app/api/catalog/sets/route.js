import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

function normalizeSet(item = {}) {
  return {
    id: item.id,
    code: item.code,
    set_code: item.code,
    name: item.name,
    title: item.name,
    game: item.game_slug || '',
    game_slug: item.game_slug || '',
    card_count: Number(item.card_count || item.count || item.total_cards || 0),
  }
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)

  const upstream = await callInternalApi('/api/v1/sets', {
    params: {
      game: searchParams.get('game') || '',
      q: searchParams.get('q') || '',
      limit: searchParams.get('limit') || 500,
      offset: searchParams.get('offset') || 0,
    },
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)

    return NextResponse.json(
      {
        error: 'catalog_sets_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  const items = (Array.isArray(upstream.payload) ? upstream.payload : upstream.payload?.items || []).map(normalizeSet)

  return NextResponse.json({ items })
}
