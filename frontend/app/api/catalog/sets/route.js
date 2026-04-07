import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

function normalizeSetCode(value) {
  return String(value || '').trim().toLowerCase()
}

function toCount(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function pickDisplayName(...candidates) {
  const values = candidates
    .map((value) => String(value || '').trim())
    .filter(Boolean)

  const firstNonNumeric = values.find((value) => !/^\d+$/.test(value))
  return firstNonNumeric || values[0] || ''
}

function normalizeSet(item = {}, searchFallback = null) {
  const code = String(item.code || item.set_code || searchFallback?.set_code || '').trim()
  const displayName = pickDisplayName(
    item.name,
    item.title,
    item.set_name,
    searchFallback?.title,
    searchFallback?.set_name,
    searchFallback?.subtitle,
    code,
    `Set #${item.id || searchFallback?.id || ''}`.trim(),
  )
  const baseCount = toCount(item.card_count ?? item.count ?? item.total_cards, 0)
  const searchCount = toCount(searchFallback?.variant_count ?? searchFallback?.card_count, 0)

  return {
    id: item.id ?? searchFallback?.id,
    code,
    set_code: code,
    name: displayName,
    title: displayName,
    game: item.game_slug || searchFallback?.game || '',
    game_slug: item.game_slug || searchFallback?.game || '',
    card_count: Math.max(baseCount, searchCount),
  }
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)

  const game = searchParams.get('game') || ''
  const q = searchParams.get('q') || ''
  const limit = searchParams.get('limit') || 500
  const offset = searchParams.get('offset') || 0

  const upstream = await callInternalApi('/api/v1/sets', {
    params: {
      game,
      q,
      limit,
      offset,
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

  const baseItems = Array.isArray(upstream.payload) ? upstream.payload : upstream.payload?.items || []
  const needsSearchFallback = baseItems.some((item) => {
    const count = toCount(item?.card_count ?? item?.count ?? item?.total_cards, 0)
    const candidateName = pickDisplayName(item?.name, item?.title, item?.set_name)
    return count <= 0 || /^\d+$/.test(candidateName)
  })

  let searchItems = []
  if (needsSearchFallback) {
    const searchUpstream = await callInternalApi('/api/v1/search', {
      params: { game, q, type: 'set', limit, offset },
    })
    if (searchUpstream.ok) {
      searchItems = Array.isArray(searchUpstream.payload) ? searchUpstream.payload : searchUpstream.payload?.items || []
    }
  }

  const searchByCode = new Map(searchItems.map((item) => [normalizeSetCode(item.set_code), item]))
  const items = baseItems.map((item) => normalizeSet(item, searchByCode.get(normalizeSetCode(item?.code || item?.set_code)) || null))

  return NextResponse.json({ items })
}
