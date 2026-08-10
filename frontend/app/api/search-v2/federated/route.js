import { NextResponse } from 'next/server'
import { callInternalApi, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const DEFAULT_PAGE_SIZE = 24
const MAX_PAGE_SIZE = 48

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
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

function safeCardmarketUrl(websitePath) {
  const raw = String(websitePath || '').trim()
  if (!raw) return null
  if (raw.startsWith('/')) return `https://www.cardmarket.com${raw}`
  try {
    const parsed = new URL(raw)
    if (parsed.protocol === 'https:' && ['cardmarket.com', 'www.cardmarket.com'].includes(parsed.hostname.toLowerCase())) return raw
  } catch {
    return null
  }
  return null
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

  const page = boundedInt(searchParams.get('page'), 1, 1, 100)
  const limit = boundedInt(searchParams.get('limit'), DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
  const offset = (page - 1) * limit
  const category = String(searchParams.get('category') || '').trim()
  const setCode = normalizeOnePieceSetCode(q, game)

  const setsPromise = callInternalApi('/api/v1/sets', {
    params: { game, q, limit: 12, offset: 0 },
    timeoutMs: 12000,
  })
  const matchesPromise = callInternalApi('/api/v2/search', {
    params: { q, game, limit: 12 },
    timeoutMs: 12000,
  })
  const singlesPromise = setCode
    ? callInternalApi('/api/v1/set-ui/prints', {
        params: { game, set_code: setCode, limit, offset },
        timeoutMs: 15000,
      })
    : callInternalApi('/api/v2/search/advanced', {
        method: 'POST',
        body: { game, q, filters: {}, limit, offset },
        timeoutMs: 15000,
      })
  const sealedPromise = setCode
    ? callInternalApi(`/api/v1/market/sets/${encodeURIComponent(game)}/${encodeURIComponent(setCode)}/products`, {
        params: { limit, offset, category },
        timeoutMs: 15000,
      })
    : callInternalApi('/api/v1/market/products', {
        params: { game, group: 'non_single', q, limit, offset, category },
        timeoutMs: 15000,
      })

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

  let marketByPrint = new Map()
  if (singles.length) {
    const ids = singles.map((item) => item.print_id).filter(Boolean)
    const marketUpstream = await callInternalApi('/api/v1/market/prints/cardmarket/batch', {
      method: 'POST',
      body: { print_ids: ids },
      timeoutMs: 15000,
    })
    if (marketUpstream.ok) {
      marketByPrint = new Map(
        toItems(marketUpstream.payload).map((item) => [String(item.print_id), item]),
      )
    }
  }

  const enrichedSingles = singles.map((item) => ({
    ...item,
    market: marketByPrint.get(String(item.print_id)) || {
      print_id: item.print_id,
      status: 'unavailable',
      reference: null,
      price: null,
      reason: 'cardmarket_reference_request_unavailable',
    },
  }))

  const singlesTotal = Number(singlesUpstream.payload?.total ?? singlesUpstream.payload?.count ?? singles.length)
  const sealedTotal = Number(sealedUpstream.payload?.total ?? sealed.length)
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
    set_intent: setCode ? { set_code: setCode, exact_set_found: Boolean(exactSet) } : null,
    counts: {
      singles: singlesTotal,
      sets: sets.length,
      sealed: sealedTotal,
      matches: matches.length,
    },
    sets,
    exact_set: exactSet,
    singles: { items: enrichedSingles, total: singlesTotal, page, limit },
    sealed: {
      items: sealed,
      total: sealedTotal,
      page,
      limit,
      categories,
      regions: sealedUpstream.payload?.regions || [],
      expansion_ids: sealedUpstream.payload?.expansion_ids || [],
    },
    matches,
    errors,
  })
}
