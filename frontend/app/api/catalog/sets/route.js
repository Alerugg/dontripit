import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

function normalizeSetCode(value) {
  return String(value || '').trim().toLowerCase()
}

function toCount(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function isNumericLike(value) {
  return /^\d+$/.test(String(value || '').trim())
}

function pickDisplayName(...candidates) {
  const values = candidates
    .map((value) => String(value || '').trim())
    .filter(Boolean)

  const firstNonNumeric = values.find((value) => !isNumericLike(value))
  return firstNonNumeric || values[0] || ''
}

function pickSetCode(item = {}, searchFallback = null) {
  const candidates = [
    item.code,
    item.set_code,
    searchFallback?.set_code,
    searchFallback?.code,
    searchFallback?.subtitle,
  ]
    .map((value) => String(value || '').trim())
    .filter(Boolean)

  const firstUseful = candidates.find((value) => !isNumericLike(value))
  return firstUseful || candidates[0] || ''
}

function normalizeSet(item = {}, searchFallback = null) {
  const code = pickSetCode(item, searchFallback)
  const displayName = pickDisplayName(
    item.name,
    item.title,
    item.set_name,
    searchFallback?.title,
    searchFallback?.name,
    searchFallback?.set_name,
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

function buildSearchMaps(searchItems = []) {
  const byCode = new Map()
  const byId = new Map()

  searchItems.forEach((item) => {
    const codeKey = normalizeSetCode(item?.set_code || item?.code)
    if (codeKey && !byCode.has(codeKey)) byCode.set(codeKey, item)

    const idKey = String(item?.id || '').trim()
    if (idKey && !byId.has(idKey)) byId.set(idKey, item)
  })

  return { byCode, byId }
}

async function fetchItemFallbacks(game, candidates = []) {
  const fallbackById = new Map()

  await Promise.all(candidates.map(async (item) => {
    const query = String(item?.code || item?.set_code || item?.name || item?.id || '').trim()
    if (!query) return

    const response = await callInternalApi('/api/v1/search', {
      params: {
        game,
        q: query,
        type: 'set',
        limit: 12,
        offset: 0,
      },
    })

    if (!response.ok) return

    const results = Array.isArray(response.payload) ? response.payload : response.payload?.items || []
    const itemId = String(item?.id || '').trim()
    const itemCode = normalizeSetCode(item?.code || item?.set_code)

    const matched = results.find((result) => {
      const resultId = String(result?.id || '').trim()
      const resultCode = normalizeSetCode(result?.set_code || result?.code)
      return (itemId && resultId === itemId) || (itemCode && resultCode === itemCode)
    })

    if (matched && itemId) {
      fallbackById.set(itemId, matched)
    }
  }))

  return fallbackById
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
  const fallbackCandidates = baseItems.filter((item) => {
    const count = toCount(item?.card_count ?? item?.count ?? item?.total_cards, 0)
    const candidateName = pickDisplayName(item?.name, item?.title, item?.set_name)
    const candidateCode = pickSetCode(item)
    return count <= 0 || isNumericLike(candidateName) || isNumericLike(candidateCode)
  })

  let searchItems = []
  if (fallbackCandidates.length > 0 && q) {
    const searchUpstream = await callInternalApi('/api/v1/search', {
      params: { game, q, type: 'set', limit, offset },
    })
    if (searchUpstream.ok) {
      searchItems = Array.isArray(searchUpstream.payload) ? searchUpstream.payload : searchUpstream.payload?.items || []
    }
  }

  const itemFallbacks = q ? new Map() : await fetchItemFallbacks(game, fallbackCandidates)

  const searchMaps = buildSearchMaps(searchItems)
  const items = baseItems.map((item) => {
    const codeKey = normalizeSetCode(item?.code || item?.set_code)
    const idKey = String(item?.id || '').trim()
    const searchFallback = searchMaps.byCode.get(codeKey) || searchMaps.byId.get(idKey) || itemFallbacks.get(idKey) || null
    return normalizeSet(item, searchFallback)
  })

  return NextResponse.json({ items })
}
