import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const MAX_PAGE_SIZE = 50
const PRINT_BATCH_SIZE = 100
const MAX_SET_PRINTS = 5000
const PUBLIC_CACHE_HEADERS = { 'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300' }

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
}

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

function cleanText(value) {
  return String(value || '').trim()
}

function normalized(value) {
  return cleanText(value)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
}

function exactMarketPrice(item = {}) {
  const raw = item?.market?.display_price
  const value = raw === null || raw === undefined || raw === '' ? null : Number(raw)
  return Number.isFinite(value) ? value : null
}

function normalizePrint(item = {}) {
  const cardName = cleanText(item.card_name || item.name) || null
  const exactVariant = item.variant && item.variant !== 'default' ? item.variant : null
  const physicalReleases = Array.isArray(item.physical_releases) ? item.physical_releases : []
  const printId = item.print_id || item.id

  return {
    ...item,
    id: printId,
    print_id: printId,
    card_id: item.card_id || null,
    type: 'print',
    name: cardName,
    title: cardName,
    variant: exactVariant,
    exact_variant: exactVariant,
    primary_image_url: item.primary_image_url || item.image_url,
    variant_count: 1,
    finish: item.finish || (item.is_foil ? 'foil' : 'non-foil'),
    physical_releases: physicalReleases,
    physical_release_names: Array.isArray(item.physical_release_names)
      ? item.physical_release_names
      : physicalReleases.map((release) => release?.name).filter(Boolean),
  }
}

async function loadAllSetPrints(game, setCode) {
  const rows = []
  const seen = new Set()
  let upstreamTotal = null
  let offset = 0
  let lastPayload = null
  let stalled = false

  while (rows.length < MAX_SET_PRINTS) {
    const response = await callInternalApi('/api/v1/set-ui/prints', {
      params: {
        game,
        set_code: setCode,
        q: '',
        sort: 'number_asc',
        limit: PRINT_BATCH_SIZE,
        offset,
      },
      timeoutMs: 20000,
    })

    if (!response.ok) return { response, rows: [], total: 0, truncated: false, scope: null }

    lastPayload = response.payload
    const batch = toItems(response.payload)
    const declaredTotal = Number(response.payload?.total)
    if (Number.isFinite(declaredTotal)) upstreamTotal = declaredTotal

    let added = 0
    for (const raw of batch) {
      const print = normalizePrint(raw)
      const key = String(print.print_id || '')
      if (!key || seen.has(key)) continue
      seen.add(key)
      rows.push(print)
      added += 1
      if (rows.length >= MAX_SET_PRINTS) break
    }

    if (!batch.length) break
    if (!added) {
      stalled = true
      break
    }

    offset += batch.length
    if (upstreamTotal !== null && offset >= upstreamTotal) break
    if (response.payload?.has_more === false) break
    if (upstreamTotal === null && response.payload?.has_more !== true && batch.length < PRINT_BATCH_SIZE) break
  }

  const truncated = stalled || (upstreamTotal !== null && rows.length < upstreamTotal)
  return {
    response: { ok: true, status: 200, payload: lastPayload },
    rows,
    total: upstreamTotal ?? rows.length,
    truncated,
    scope: lastPayload?.scope || null,
  }
}

function printSearchText(print) {
  return normalized([
    print.name,
    print.collector_number,
    print.rarity,
    print.language,
    print.finish,
    print.variant,
    print.set_code,
    ...(print.physical_release_names || []),
  ].filter(Boolean).join(' '))
}

function cardSearchText(card) {
  return normalized([
    card.name,
    card.collector_number,
    card.rarity,
    card.set_code,
  ].filter(Boolean).join(' '))
}

function compareText(left, right, direction = 1) {
  return String(left || '').localeCompare(String(right || ''), undefined, {
    numeric: true,
    sensitivity: 'base',
  }) * direction
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

function buildCanonicalCards(prints) {
  const groups = new Map()

  for (const print of prints) {
    if (!print.card_id) continue
    const key = String(print.card_id)
    const group = groups.get(key) || []
    group.push(print)
    groups.set(key, group)
  }

  return [...groups.entries()].map(([cardId, variants]) => {
    const representative = variants.find((item) => item.primary_image_url) || variants[0]
    const prices = variants.map(exactMarketPrice).filter((value) => value !== null)
    const languages = [...new Set(variants.map((item) => cleanText(item.language).toLowerCase()).filter(Boolean))]
    const finishes = [...new Set(variants.map((item) => cleanText(item.finish).toLowerCase()).filter(Boolean))]

    return {
      id: cardId,
      card_id: cardId,
      type: 'card',
      game: representative.game,
      name: representative.name,
      title: representative.name,
      primary_image_url: representative.primary_image_url,
      set_code: representative.set_code,
      set_name: representative.set_name,
      collector_number: representative.collector_number,
      rarity: representative.rarity,
      variant_count: variants.length,
      priced_count: prices.length,
      price_coverage: variants.length ? Math.round((prices.length / variants.length) * 100) : 0,
      min_exact_price: prices.length ? Math.min(...prices) : null,
      max_exact_price: prices.length ? Math.max(...prices) : null,
      languages,
      finishes,
      _prints: variants,
    }
  })
}

function matchesPhysicalFilters(print, { language, finish, rarity, pricedOnly }) {
  if (language && normalized(print.language) !== normalized(language)) return false
  if (finish && normalized(print.finish) !== normalized(finish)) return false
  if (rarity && normalized(print.rarity) !== normalized(rarity)) return false
  if (pricedOnly && exactMarketPrice(print) === null) return false
  return true
}

function sortItems(items, sort, kind) {
  return [...items].sort((left, right) => {
    if (sort === 'name_asc') return compareText(left.name, right.name, 1)
    if (sort === 'name_desc') return compareText(left.name, right.name, -1)
    if (sort === 'number_desc') return compareText(left.collector_number, right.collector_number, -1)
    if (sort === 'price_asc') {
      const leftPrice = kind === 'card' ? left.min_exact_price : exactMarketPrice(left)
      const rightPrice = kind === 'card' ? right.min_exact_price : exactMarketPrice(right)
      return compareNullableNumber(leftPrice, rightPrice, 1)
    }
    if (sort === 'price_desc') {
      const leftPrice = kind === 'card' ? left.max_exact_price : exactMarketPrice(left)
      const rightPrice = kind === 'card' ? right.max_exact_price : exactMarketPrice(right)
      return compareNullableNumber(leftPrice, rightPrice, -1)
    }
    if (sort === 'coverage_desc' && kind === 'card') return compareNullableNumber(left.price_coverage, right.price_coverage, -1)
    return compareText(left.collector_number, right.collector_number, 1)
  })
}

function cleanCardForResponse(card) {
  const { _prints, ...clean } = card
  return clean
}

function uniqueFacet(values) {
  return [...new Set(values.map((value) => cleanText(value)).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }))
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = searchParams.get('game') || ''
  const setCode = searchParams.get('set_code') || ''
  const q = searchParams.get('q') || ''
  const kind = searchParams.get('kind') === 'print' ? 'print' : 'card'
  const sort = searchParams.get('sort') || 'number_asc'
  const language = searchParams.get('language') || ''
  const finish = searchParams.get('finish') || ''
  const rarity = searchParams.get('rarity') || ''
  const pricedOnly = searchParams.get('priced') === '1'
  const limit = boundedInt(searchParams.get('limit'), 24, 1, MAX_PAGE_SIZE)
  const offset = boundedInt(searchParams.get('offset'), 0, 0, 100000)

  if (!setCode) {
    return NextResponse.json({ error: 'set_code_required', message: 'Missing set_code query param.' }, { status: 400 })
  }

  const [setUpstream, allPrints] = await Promise.all([
    callInternalApi('/api/v1/sets', {
      params: { game, q: setCode, limit: 50, offset: 0 },
    }),
    loadAllSetPrints(game, setCode),
  ])

  if (!setUpstream.ok) {
    const developerHint = getDeveloperErrorHint(setUpstream.payload, setUpstream.status)
    return NextResponse.json(
      {
        error: 'catalog_set_lookup_failed',
        message: getPublicErrorMessage(setUpstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: setUpstream.status },
    )
  }

  if (!allPrints.response.ok) {
    const developerHint = getDeveloperErrorHint(allPrints.response.payload, allPrints.response.status)
    return NextResponse.json(
      {
        error: 'catalog_set_prints_failed',
        message: getPublicErrorMessage(allPrints.response.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: allPrints.response.status },
    )
  }

  const sets = toItems(setUpstream.payload)
  const set = sets.find((item) => String(item?.code || '').toLowerCase() === String(setCode).toLowerCase()) || sets[0] || null
  const rawPrints = allPrints.rows
  const canonicalCards = buildCanonicalCards(rawPrints)
  const exactPricedPrints = rawPrints.filter((item) => exactMarketPrice(item) !== null)
  const query = normalized(q)
  const physicalFilters = { language, finish, rarity, pricedOnly }

  let resultItems
  if (kind === 'print') {
    resultItems = rawPrints
      .filter((print) => !query || printSearchText(print).includes(query))
      .filter((print) => matchesPhysicalFilters(print, physicalFilters))
  } else {
    resultItems = canonicalCards
      .filter((card) => !query || cardSearchText(card).includes(query))
      .map((card) => {
        const matchingPrints = card._prints.filter((print) => matchesPhysicalFilters(print, physicalFilters))
        if (!matchingPrints.length && (language || finish || rarity || pricedOnly)) return null
        if (!(language || finish || rarity || pricedOnly)) return card

        const prices = matchingPrints.map(exactMarketPrice).filter((value) => value !== null)
        return {
          ...card,
          variant_count: matchingPrints.length,
          priced_count: prices.length,
          price_coverage: matchingPrints.length ? Math.round((prices.length / matchingPrints.length) * 100) : 0,
          min_exact_price: prices.length ? Math.min(...prices) : null,
          max_exact_price: prices.length ? Math.max(...prices) : null,
        }
      })
      .filter(Boolean)
  }

  resultItems = sortItems(resultItems, sort, kind)
  const total = resultItems.length
  const pageItems = resultItems.slice(offset, offset + limit).map((item) => kind === 'card' ? cleanCardForResponse(item) : item)

  const facets = {
    languages: uniqueFacet(rawPrints.map((item) => String(item.language || '').toUpperCase())),
    finishes: uniqueFacet(rawPrints.map((item) => item.finish)),
    rarities: uniqueFacet(rawPrints.map((item) => item.rarity)),
  }

  const stats = {
    cards: canonicalCards.length,
    prints: rawPrints.length,
    priced_prints: exactPricedPrints.length,
    price_coverage: rawPrints.length ? Math.round((exactPricedPrints.length / rawPrints.length) * 100) : 0,
    languages: facets.languages,
  }

  return NextResponse.json({
    set: set
      ? {
          id: set.id,
          code: set.code,
          name: set.name,
          game_slug: set.game_slug || game,
          release_date: set.release_date || set.released_at || null,
          series: set.series || set.block || '',
          card_count: stats.cards || set.card_count || set.total_cards || null,
          print_count: stats.prints,
          collector_total: allPrints.total,
        }
      : null,
    kind,
    items: pageItems,
    cards: kind === 'card' ? pageItems : [],
    prints: kind === 'print' ? pageItems : [],
    stats,
    facets,
    total,
    limit,
    offset,
    has_more: offset + pageItems.length < total,
    truncated: allPrints.truncated,
    scope: allPrints.scope,
  }, { headers: PUBLIC_CACHE_HEADERS })
}
