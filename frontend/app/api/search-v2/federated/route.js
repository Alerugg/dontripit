import { NextResponse } from 'next/server'
import { callInternalApi, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'
import { marketFromSearchItem, safeCardmarketUrl } from '../../../../lib/searchV2/market'

const DEFAULT_PAGE_SIZE = 24
const MAX_PAGE_SIZE = 48
const RESULT_KINDS = new Set(['all', 'singles', 'sets', 'sealed', 'matches'])
const SORTS = new Set(['relevance', 'price_desc', 'price_asc', 'number_asc', 'number_desc', 'name_asc', 'name_desc'])

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
}

function truthy(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase())
}

function normalizeOnePieceSetCode(query, game) {
  if (String(game || '').toLowerCase() !== 'onepiece') return null
  const match = String(query || '').trim().match(/^(OP|ST|EB|PRB)[\s_-]?(\d{1,3})$/i)
  if (!match) return null
  const [, prefix, rawNumber] = match
  return `${prefix.toLowerCase()}-${String(Number(rawNumber)).padStart(2, '0')}`
}

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

function skipped(payload = { items: [], total: null }) {
  return { ok: true, status: 200, payload, skipped: true }
}

function normalizePrint(item = {}) {
  const cardName = item.card_name || item.name || item.title || ''
  const exactVariant = item.exact_variant || (item.variant && item.variant !== 'default' ? item.variant : null)
  return {
    ...item,
    type: 'print',
    id: item.print_id || item.id,
    print_id: item.print_id || item.id,
    name: cardName || `Carta #${item.collector_number || item.card_id || item.id}`,
    title: cardName || `Carta #${item.collector_number || item.card_id || item.id}`,
    exact_variant: exactVariant,
    primary_image_url: item.primary_image_url || item.image_url,
    variant_count: 1,
  }
}

function normalizeSealed(item = {}) {
  const minimum = item.price?.minimum ?? item.price_low ?? null
  const conservative = item.price?.conservative ?? item.price_mid ?? null
  const trend = item.price?.trend ?? item.price_market ?? null
  const average = item.price?.average ?? item.price_last ?? null
  const value = item.price?.value ?? conservative ?? trend ?? average ?? minimum
  const websitePath = item.cardmarket?.website_path || item.website_path || null
  const cardmarket = item.cardmarket || {
    provider: 'cardmarket',
    id_product: item.external_id ? String(item.external_id) : null,
    website_path: websitePath,
    url: safeCardmarketUrl(websitePath),
  }

  return {
    ...item,
    type: 'sealed',
    price: value === null || value === undefined ? null : {
      value,
      minimum,
      conservative,
      trend,
      average,
      currency: item.price?.currency || item.currency || 'EUR',
      source: 'cardmarket',
      as_of: item.price?.as_of || item.price_as_of || null,
    },
    cardmarket: {
      ...cardmarket,
      url: cardmarket?.url || safeCardmarketUrl(websitePath),
    },
  }
}

function categoryOptions(items = []) {
  const counts = new Map()
  items.forEach((item) => {
    const value = String(item.category || '').trim()
    if (!value) return
    counts.set(value, (counts.get(value) || 0) + 1)
  })
  return [...counts.entries()].map(([value, count]) => ({ value, count }))
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const q = String(searchParams.get('q') || '').trim()
  const game = String(searchParams.get('game') || '').trim().toLowerCase()
  if (!q || !game) {
    return NextResponse.json({ error: 'invalid_params', message: 'q and game are required.' }, { status: 400 })
  }

  const page = boundedInt(searchParams.get('page'), 1, 1, 5000)
  const limit = boundedInt(searchParams.get('limit'), DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
  const offset = (page - 1) * limit
  const requestedKind = String(searchParams.get('kind') || 'all').trim().toLowerCase()
  const kind = RESULT_KINDS.has(requestedKind) ? requestedKind : 'all'
  const requestedSort = String(searchParams.get('sort') || 'relevance').trim().toLowerCase()
  const sort = SORTS.has(requestedSort) ? requestedSort : 'relevance'
  const hasPrice = truthy(searchParams.get('has_price'))
  const category = String(searchParams.get('category') || '').trim()
  const region = String(searchParams.get('region') || '').trim().toLowerCase()
  const setCode = normalizeOnePieceSetCode(q, game)

  const needsSingles = kind === 'singles' || kind === 'all'
  const needsSealed = kind === 'sealed' || (kind === 'all' && page === 1)
  const needsMatches = kind === 'matches' || (kind === 'all' && page === 1)
  const setsOffset = kind === 'sets' ? offset : 0
  const setsLimit = kind === 'sets' ? limit : 12

  // Sets are inexpensive and keep their tab count stable while the user pages
  // singles/sealed. Heavy singles/sealed readers are only called when visible.
  const setsPromise = callInternalApi('/api/v1/sets', {
    params: { game, q, limit: setsLimit, offset: setsOffset, meta: 1 },
    timeoutMs: 12000,
  })
  const matchesPromise = needsMatches
    ? callInternalApi('/api/v2/search', {
        params: { q, game, limit: 12 },
        timeoutMs: 12000,
      })
    : Promise.resolve(skipped({ items: [], total: null }))
  const singlesPromise = needsSingles
    ? (setCode
        ? callInternalApi('/api/v1/set-ui/prints', {
            params: { game, set_code: setCode, sort, has_price: hasPrice ? 1 : '', limit, offset },
            timeoutMs: 20000,
          })
        : callInternalApi('/api/v2/search/advanced', {
            method: 'POST',
            body: { game, q, filters: {}, sort, has_price: hasPrice, limit, offset },
            timeoutMs: 20000,
          }))
    : Promise.resolve(skipped({ items: [], total: null }))
  const sealedPromise = needsSealed
    ? (setCode
        ? callInternalApi(`/api/v1/market/set-products/${encodeURIComponent(game)}/${encodeURIComponent(setCode)}`, {
            params: { limit, offset: kind === 'sealed' ? offset : 0, category, region },
            timeoutMs: 15000,
          })
        : callInternalApi('/api/v1/market/current-products', {
            params: { game, group: 'non_single', q, limit, offset: kind === 'sealed' ? offset : 0, category },
            timeoutMs: 15000,
          }))
    : Promise.resolve(skipped({ items: [], total: null, categories: [] }))

  const [setsUpstream, matchesUpstream, singlesUpstream, sealedUpstream] = await Promise.all([
    setsPromise,
    matchesPromise,
    singlesPromise,
    sealedPromise,
  ])

  const sets = setsUpstream.ok ? toItems(setsUpstream.payload) : []
  const exactSet = setCode
    ? sets.find((item) => String(item?.code || '').toLowerCase() === setCode) || null
    : null
  const singles = singlesUpstream.ok ? toItems(singlesUpstream.payload).map(normalizePrint) : []
  const matches = matchesUpstream.ok ? toItems(matchesUpstream.payload) : []
  const sealed = sealedUpstream.ok ? toItems(sealedUpstream.payload).map(normalizeSealed) : []
  const enrichedSingles = singles.map((item) => ({ ...item, market: marketFromSearchItem(item) }))

  const singlesTotal = singlesUpstream.skipped
    ? null
    : Number(singlesUpstream.payload?.total ?? singlesUpstream.payload?.count ?? singles.length)
  const setsTotal = Number(setsUpstream.payload?.total ?? sets.length)
  const sealedTotal = sealedUpstream.skipped
    ? null
    : Number(sealedUpstream.payload?.total ?? sealed.length)
  const matchesTotal = matchesUpstream.skipped ? null : Number(matchesUpstream.payload?.total ?? matches.length)
  const categories = Array.isArray(sealedUpstream.payload?.categories)
    ? sealedUpstream.payload.categories
    : categoryOptions(sealed)

  const errors = {}
  if (!setsUpstream.ok) errors.sets = getPublicErrorMessage(setsUpstream.status)
  if (!matchesUpstream.ok) errors.matches = getPublicErrorMessage(matchesUpstream.status)
  if (!singlesUpstream.ok) errors.singles = getPublicErrorMessage(singlesUpstream.status)
  if (!sealedUpstream.ok) errors.sealed = getPublicErrorMessage(sealedUpstream.status)

  if (!setsUpstream.ok && !matchesUpstream.ok && !singlesUpstream.ok && !sealedUpstream.ok) {
    return NextResponse.json({ error: 'federated_search_failed', message: 'No pudimos consultar el catálogo.' }, { status: 503 })
  }

  return NextResponse.json({
    query: q,
    game,
    page,
    limit,
    kind,
    sort,
    has_price: hasPrice,
    set_intent: setCode ? { set_code: setCode, exact_set_found: Boolean(exactSet) } : null,
    counts: {
      singles: singlesTotal,
      sets: setsTotal,
      sealed: sealedTotal,
      matches: matchesTotal,
    },
    sets,
    sets_page: { page: kind === 'sets' ? page : 1, total: setsTotal, limit: setsLimit },
    exact_set: exactSet,
    singles: { items: enrichedSingles, total: singlesTotal, page: needsSingles ? page : 1, limit },
    sealed: {
      items: sealed,
      total: sealedTotal,
      page: kind === 'sealed' ? page : 1,
      limit,
      categories,
      regions: sealedUpstream.payload?.regions || [],
      expansion_ids: sealedUpstream.payload?.expansion_ids || [],
    },
    matches,
    errors,
  })
}
