import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

function normalizeSet(item = {}) {
  const code = String(item.code || item.set_code || '').trim()
  const rawName = String(item.name || item.title || item.set_name || '').trim()
  const isNumericName = /^\d+$/.test(rawName)
  const fallbackName = code || `Set #${item.id || ''}`.trim()
  const displayName = rawName && !(isNumericName && code) ? rawName : fallbackName

  return {
    id: item.id,
    code,
    set_code: code,
    name: displayName,
    title: displayName,
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
