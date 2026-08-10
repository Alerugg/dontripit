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

const INTERNAL_PAGE_SIZE = 50
const MAX_WEB_SETS = 1000

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

async function fetchAllSets({ game, q, requestedLimit, requestedOffset }) {
  const items = []
  const maxItems = Math.min(Math.max(Number(requestedLimit) || 500, 1), MAX_WEB_SETS)
  let offset = Math.max(Number(requestedOffset) || 0, 0)

  while (items.length < maxItems) {
    const pageLimit = Math.min(INTERNAL_PAGE_SIZE, maxItems - items.length)
    const response = await callInternalApi('/api/v1/sets', {
      params: { game, q, limit: pageLimit, offset },
      timeoutMs: 20000,
    })
    if (!response.ok) return { ok: false, response }

    const page = toItems(response.payload)
    items.push(...page)
    if (page.length < pageLimit) break
    offset += page.length
    if (offset > 1000) break
  }

  return { ok: true, items }
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
  const limit = searchParams.get('limit') || 500
  const offset = searchParams.get('offset') || 0

  const paged = await fetchAllSets({ game, q, requestedLimit: limit, requestedOffset: offset })

  if (!paged.ok) {
    const upstream = paged.response
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

  const baseItems = paged.items
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

  const itemFallbacks = q ? new Map() : await fetchItemFallbacks(game, fallbackCandidates.slice(0, 80))
  const searchMaps = buildSearchMaps(searchItems)
  const items = baseItems.map((item) => {
    const codeKey = normalizeSetCode(item?.code || item?.set_code)
    const idKey = String(item?.id || '').trim()
    const searchFallback = searchMaps.byCode.get(codeKey) || searchMaps.byId.get(idKey) || itemFallbacks.get(idKey) || null
    return normalizeSet(item, searchFallback)
  })

  return NextResponse.json({ items, count: items.length })
}
