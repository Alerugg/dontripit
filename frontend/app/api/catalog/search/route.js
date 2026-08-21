import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const SEARCH_BATCH = 100
const MAX_KIND_RESULTS = 5000
const MAX_PAGE_SIZE = 50
const MARKET_BATCH = 100
const PUBLIC_CACHE_HEADERS = { 'Cache-Control': 'public, s-maxage=30, stale-while-revalidate=120' }

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
}

function toItems(payload) {
  if (Array.isArray(payload)) return payload
  return payload?.items || payload?.results || []
}

function normalizeV2Card(item = {}) {
  const matched = item.matched_print || {}
  const id = item.card_id || item.id
  return {
    type: 'card',
    id,
    card_id: id,
    title: item.name || item.title,
    name: item.name || item.title,
    game: item.game,
    set_code: matched.set_code || item.set_code || null,
    set_name: matched.set_name || item.set_name || null,
    collector_number: matched.collector_number || item.collector_number || null,
    rarity: matched.rarity || item.rarity || null,
    primary_image_url: matched.primary_image_url || item.primary_image_url || item.image_url || null,
    variant_count: Number(item.variant_count || 0),
    score: item.score ?? null,
  }
}

function marketPrice(item) {
  const raw = item?.market?.display_price
  if (raw === null || raw === undefined || raw === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function compareNullableNumber(left, right, direction = 1) {
  const a = left === null || left === undefined || left === '' ? null : Number(left)
  const b = right === null || right === undefined || right === '' ? null : Number(right)
  const validA = a !== null && Number.isFinite(a)
  const validB = b !== null && Number.isFinite(b)
  if (!validA && !validB) return 0
  if (!validA) return 1
  if (!validB) return -1
  return (a - b) * direction
}

function compareText(left, right, direction = 1) {
  return String(left || '').localeCompare(String(right || ''), undefined, {
    numeric: true,
    sensitivity: 'base',
  }) * direction
}

function sortRows(rows, sort) {
  if (!sort || sort === 'relevance') return rows
  return [...rows].sort((left, right) => {
    if (sort === 'price_desc') return compareNullableNumber(marketPrice(left), marketPrice(right), -1)
    if (sort === 'price_asc') return compareNullableNumber(marketPrice(left), marketPrice(right), 1)
    if (sort === 'collector_desc') return compareText(left?.collector_number, right?.collector_number, -1)
    if (sort === 'collector_asc') return compareText(left?.collector_number, right?.collector_number, 1)
    if (sort === 'name_desc') return compareText(left?.title || left?.name, right?.title || right?.name, -1)
    if (sort === 'name_asc') return compareText(left?.title || left?.name, right?.title || right?.name, 1)
    return 0
  })
}

function uniqueRows(rows) {
  const seen = new Set()
  const output = []
  for (const item of rows) {
    const key = `${item?.type || 'unknown'}:${item?.id ?? item?.card_id ?? item?.set_code ?? item?.title}`
    if (seen.has(key)) continue
    seen.add(key)
    output.push(item)
  }
  return output
}

function legacyBatchSize(q) {
  const length = String(q || '').trim().length
  if (length <= 1) return 12
  if (length === 2) return 24
  return SEARCH_BATCH
}

async function fetchAllLegacyRows({ q, game, type }) {
  const rows = []
  const batchSize = legacyBatchSize(q)
  let offset = 0
  let truncated = false
  let consecutiveEmptyPages = 0

  while (rows.length < MAX_KIND_RESULTS && consecutiveEmptyPages < 2) {
    const upstream = await callInternalApi('/api/v1/search', {
      params: { q, game, type, limit: batchSize, offset },
      timeoutMs: 20000,
    })
    if (!upstream.ok) return { ok: false, upstream, rows: [], truncated: false }

    const batch = toItems(upstream.payload).filter((item) => !type || item?.type === type)
    if (!batch.length) consecutiveEmptyPages += 1
    else consecutiveEmptyPages = 0

    const before = rows.length
    rows.push(...batch)
    const deduped = uniqueRows(rows)
    rows.length = 0
    rows.push(...deduped)

    offset += batchSize
    if (batch.length && rows.length === before) {
      truncated = true
      break
    }
  }

  if (rows.length >= MAX_KIND_RESULTS) truncated = true
  return { ok: true, rows: rows.slice(0, MAX_KIND_RESULTS), truncated }
}

async function fetchCanonicalCardSource({ q, game, requireAll = false, limit = 24, offset = 0 }) {
  const first = await callInternalApi('/api/v2/search', {
    params: { q, game, limit: requireAll ? SEARCH_BATCH : limit, offset: requireAll ? 0 : offset },
    timeoutMs: 20000,
  })
  if (!first.ok) return { ok: false, upstream: first, rows: [], total: 0, truncated: false }

  const payload = first.payload || {}
  const exactTotal = Number(payload.total)
  const canonicalMode = payload.pagination_mode === 'canonical_name' && Number.isFinite(exactTotal)

  if (!canonicalMode) {
    const fallback = await fetchAllLegacyRows({ q, game, type: 'card' })
    return fallback.ok
      ? { ...fallback, total: fallback.rows.length, canonicalMode: false }
      : fallback
  }

  if (!requireAll) {
    return {
      ok: true,
      rows: toItems(payload).map(normalizeV2Card),
      total: exactTotal,
      totalPrints: Number(payload.total_prints || 0),
      truncated: false,
      canonicalMode: true,
    }
  }

  const rows = toItems(payload).map(normalizeV2Card)
  let currentPayload = payload
  let nextOffset = Number(currentPayload.next_offset)
  while (currentPayload.has_more !== false && Number.isFinite(nextOffset) && rows.length < MAX_KIND_RESULTS) {
    const page = await callInternalApi('/api/v2/search', {
      params: { q, game, limit: SEARCH_BATCH, offset: nextOffset },
      timeoutMs: 20000,
    })
    if (!page.ok) return { ok: false, upstream: page, rows: [], total: 0, truncated: false }
    const pageItems = toItems(page.payload).map(normalizeV2Card)
    if (!pageItems.length) break
    rows.push(...pageItems)
    currentPayload = page.payload || {}
    const following = Number(currentPayload.next_offset)
    if (!currentPayload.has_more || !Number.isFinite(following) || following <= nextOffset) break
    nextOffset = following
  }

  return {
    ok: true,
    rows: uniqueRows(rows).slice(0, MAX_KIND_RESULTS),
    total: exactTotal,
    totalPrints: Number(payload.total_prints || 0),
    truncated: rows.length < exactTotal,
    canonicalMode: true,
  }
}

async function enrichPrintsWithMarket(rows) {
  if (!rows.length) return { rows, complete: true, failedUpstream: null }
  const ids = rows
    .filter((item) => item?.type === 'print' && Number.isInteger(Number(item?.id)))
    .map((item) => Number(item.id))
  if (!ids.length) return { rows, complete: true, failedUpstream: null }

  const chunks = []
  for (let index = 0; index < ids.length; index += MARKET_BATCH) chunks.push(ids.slice(index, index + MARKET_BATCH))

  const marketResults = await Promise.all(chunks.map((chunk) => callInternalApi('/api/v1/market/prints/summary', {
    params: { ids: chunk.join(',') },
    timeoutMs: 20000,
  })))
  const failedUpstream = marketResults.find((result) => !result.ok) || null

  const marketByPrint = new Map()
  for (const result of marketResults) {
    if (!result.ok) continue
    for (const row of toItems(result.payload)) marketByPrint.set(Number(row.print_id), row)
  }

  return {
    rows: rows.map((item) => {
      if (item?.type !== 'print') return item
      const market = marketByPrint.get(Number(item.id))
      return market ? { ...item, market } : item
    }),
    complete: !failedUpstream,
    failedUpstream,
  }
}

function applyPhysicalFilters(rows, { language, pricedOnly }) {
  return rows.filter((item) => {
    if (language && item?.type === 'print' && String(item?.language || '').toLowerCase() !== language) return false
    if (pricedOnly && item?.type !== 'print') return false
    if (pricedOnly && marketPrice(item) === null) return false
    return true
  })
}

function responseError(upstream) {
  const developerHint = getDeveloperErrorHint(upstream?.payload, upstream?.status)
  return NextResponse.json(
    {
      error: 'catalog_search_failed',
      message: getPublicErrorMessage(upstream?.status || 503),
      ...(developerHint ? { developer_hint: developerHint } : {}),
    },
    { status: upstream?.status || 503 },
  )
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const q = String(searchParams.get('q') || '').trim()
  const game = String(searchParams.get('game') || '').trim()
  const type = ['card', 'print', 'set'].includes(searchParams.get('type')) ? searchParams.get('type') : ''
  const language = String(searchParams.get('language') || '').trim().toLowerCase()
  const pricedOnly = searchParams.get('priced') === '1'
  const sort = String(searchParams.get('sort') || 'relevance')
  const limit = boundedInt(searchParams.get('limit'), 24, 1, MAX_PAGE_SIZE)
  const offset = boundedInt(searchParams.get('offset'), 0, 0, 100000)
  const includeCounts = searchParams.get('include_counts') !== '0'
  const countsOnly = searchParams.get('counts_only') === '1'

  if (!q) return NextResponse.json({ error: 'q_required', message: 'Escribe algo para buscar.' }, { status: 400 })

  const fastCardPage = !includeCounts
    && !countsOnly
    && type === 'card'
    && sort === 'relevance'
    && !language
    && !pricedOnly

  if (fastCardPage) {
    const cardsSource = await fetchCanonicalCardSource({ q, game, requireAll: false, limit, offset })
    if (!cardsSource.ok) return responseError(cardsSource.upstream)

    const selectedRows = cardsSource.canonicalMode
      ? cardsSource.rows
      : cardsSource.rows.slice(offset, offset + limit)
    const selectedTotal = Number(cardsSource.total || cardsSource.rows.length)
    const truncated = Boolean(cardsSource.truncated)

    return NextResponse.json({
      items: selectedRows,
      total: selectedTotal,
      counts: {
        card: selectedTotal,
        print: null,
        set: null,
        all: null,
      },
      counts_complete: false,
      limit,
      offset,
      has_more: offset + selectedRows.length < selectedTotal,
      next_offset: offset + selectedRows.length < selectedTotal ? offset + selectedRows.length : null,
      truncated,
      integrity: truncated ? `La búsqueda alcanzó el límite de seguridad de ${MAX_KIND_RESULTS.toLocaleString()} resultados por tipo.` : null,
    }, { headers: PUBLIC_CACHE_HEADERS })
  }

  const needAllCards = type === '' || (type === 'card' && sort !== 'relevance')
  const [cardsSource, printsSource, setsSource] = await Promise.all([
    fetchCanonicalCardSource({ q, game, requireAll: needAllCards, limit, offset }),
    fetchAllLegacyRows({ q, game, type: 'print' }),
    fetchAllLegacyRows({ q, game, type: 'set' }),
  ])

  for (const source of [cardsSource, printsSource, setsSource]) {
    if (!source.ok) return responseError(source.upstream)
  }

  let cards = cardsSource.rows
  let prints = printsSource.rows
  const sets = setsSource.rows
  const needsGlobalMarket = pricedOnly || sort === 'price_asc' || sort === 'price_desc'
  if (needsGlobalMarket) {
    const enriched = await enrichPrintsWithMarket(prints)
    if (!enriched.complete) return responseError(enriched.failedUpstream)
    prints = enriched.rows
  }

  const filteredPrints = applyPhysicalFilters(prints, { language, pricedOnly })
  const filteredCards = pricedOnly ? [] : cards
  const filteredSets = pricedOnly ? [] : sets
  const cardCount = cardsSource.canonicalMode ? Number(cardsSource.total || 0) : filteredCards.length
  const counts = {
    card: pricedOnly ? 0 : cardCount,
    print: filteredPrints.length,
    set: pricedOnly ? 0 : filteredSets.length,
    all: (pricedOnly ? 0 : cardCount + filteredSets.length) + filteredPrints.length,
  }
  const truncated = Boolean(cardsSource.truncated || printsSource.truncated || setsSource.truncated)
  const integrity = truncated ? `La búsqueda alcanzó el límite de seguridad de ${MAX_KIND_RESULTS.toLocaleString()} resultados por tipo.` : null

  if (countsOnly) {
    return NextResponse.json({
      counts,
      counts_complete: true,
      truncated,
      integrity,
    }, { headers: PUBLIC_CACHE_HEADERS })
  }

  let selectedRows
  let selectedTotal
  if (type === 'card') {
    selectedRows = cards
    selectedTotal = cardsSource.canonicalMode ? Number(cardsSource.total || 0) : cards.length
  } else if (type === 'print') {
    selectedRows = filteredPrints
    selectedTotal = filteredPrints.length
  } else if (type === 'set') {
    selectedRows = filteredSets
    selectedTotal = filteredSets.length
  } else {
    selectedRows = [...filteredCards, ...filteredPrints, ...filteredSets]
    selectedTotal = counts.all
  }

  if (!(type === 'card' && cardsSource.canonicalMode && sort === 'relevance')) selectedRows = sortRows(selectedRows, sort)

  let pageItems
  if (type === 'card' && cardsSource.canonicalMode && sort === 'relevance') pageItems = selectedRows
  else pageItems = selectedRows.slice(offset, offset + limit)

  if (!needsGlobalMarket) pageItems = (await enrichPrintsWithMarket(pageItems)).rows

  return NextResponse.json({
    items: pageItems,
    total: selectedTotal,
    counts,
    counts_complete: true,
    limit,
    offset,
    has_more: offset + pageItems.length < selectedTotal,
    next_offset: offset + pageItems.length < selectedTotal ? offset + pageItems.length : null,
    truncated,
    integrity,
  }, { headers: PUBLIC_CACHE_HEADERS })
}
