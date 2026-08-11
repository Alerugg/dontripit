import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'
import {
  buildSearchMaps,
  isNumericLike,
  normalizeSet,
  normalizeSetCode,
  pickDisplayName,
  pickSetCode,
  selectBestSearchFallback,
  toCount,
} from '../../../../lib/catalog/normalizers/sets'

const MAX_PAGE_SIZE = 100

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
}

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

async function fetchItemFallbacks(game, candidates = []) {
  const fallbackById = new Map()

  await Promise.all(candidates.map(async (item) => {
    const query = String(item?.code || item?.set_code || item?.name || item?.id || '').trim()
    if (!query) return

    const response = await callInternalApi('/api/v1/search', {
      params: { game, q: query, type: 'set', limit: 12, offset: 0 },
    })
    if (!response.ok) return

    const results = toItems(response.payload)
    const itemId = String(item?.id || '').trim()
    const matched = selectBestSearchFallback(item, results)
    if (matched && itemId) fallbackById.set(itemId, matched)
  }))

  return fallbackById
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = searchParams.get('game') || ''
  const q = searchParams.get('q') || ''
  const limit = boundedInt(searchParams.get('limit'), 24, 1, MAX_PAGE_SIZE)
  const offset = boundedInt(searchParams.get('offset'), 0, 0, 100000)

  const upstream = await callInternalApi('/api/v1/sets', {
    params: { game, q, limit, offset, meta: 1 },
    timeoutMs: 20000,
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

  const baseItems = toItems(upstream.payload)
  const fallbackCandidates = baseItems.filter((item) => {
    const count = toCount(item?.card_count ?? item?.count ?? item?.total_cards, 0)
    const candidateName = pickDisplayName(item?.name, item?.title, item?.set_name)
    const candidateCode = pickSetCode(item)
    return count <= 0 || isNumericLike(candidateName) || isNumericLike(candidateCode)
  })

  let searchItems = []
  if (fallbackCandidates.length > 0 && q) {
    const searchUpstream = await callInternalApi('/api/v1/search', {
      params: { game, q, type: 'set', limit: 50, offset: 0 },
    })
    if (searchUpstream.ok) searchItems = toItems(searchUpstream.payload)
  }

  const itemFallbacks = q ? new Map() : await fetchItemFallbacks(game, fallbackCandidates.slice(0, 24))
  const searchMaps = buildSearchMaps(searchItems)
  const items = baseItems.map((item) => {
    const codeKey = normalizeSetCode(item?.code || item?.set_code)
    const idKey = String(item?.id || '').trim()
    const searchFallback = searchMaps.byCode.get(codeKey) || searchMaps.byId.get(idKey) || itemFallbacks.get(idKey) || null
    return normalizeSet(item, searchFallback)
  })

  return NextResponse.json({
    items,
    count: items.length,
    total: Number(upstream.payload?.total ?? items.length),
    limit: Number(upstream.payload?.limit ?? limit),
    offset: Number(upstream.payload?.offset ?? offset),
  })
}
