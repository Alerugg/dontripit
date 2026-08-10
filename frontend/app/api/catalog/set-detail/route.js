import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const INTERNAL_PAGE_SIZE = 50
const MAX_SET_PRINTS = 1000

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

function normalizePrint(item = {}, searchProfile = null) {
  const profile = searchProfile || null
  const cardName = profile?.name || item.card_name || item.card?.name || item.name || item.title || ''
  const setName = profile?.set_name || item.set_name || item.set_code || ''
  const exactVariant = profile?.exact_variant || item.variant || null
  const family = profile?.variant_family || null
  const finish = profile?.attributes?.finish || (item.is_foil ? 'foil' : 'non-foil')

  return {
    ...item,
    ...(profile || {}),
    id: profile?.print_id || item.id,
    print_id: profile?.print_id || item.id,
    card_id: profile?.card_id || item.card_id,
    type: 'print',
    name: cardName || `Carta #${item.collector_number || item.card_id || item.id}`,
    title: cardName || `Carta #${item.collector_number || item.card_id || item.id}`,
    set_name: setName,
    set_code: profile?.set_code || item.set_code,
    collector_number: profile?.collector_number || item.collector_number,
    language: profile?.language || item.language,
    rarity: profile?.rarity || item.rarity,
    variant: exactVariant,
    exact_variant: exactVariant,
    variant_family: family,
    primary_image_url: profile?.primary_image_url || item.primary_image_url || item.image_url,
    variant_count: 1,
    finish,
  }
}

async function fetchAllPrints({ game, setCode, requestedLimit, requestedOffset }) {
  const rows = []
  const maxItems = Math.min(Math.max(Number(requestedLimit) || 200, 1), MAX_SET_PRINTS)
  let offset = Math.max(Number(requestedOffset) || 0, 0)

  while (rows.length < maxItems) {
    const pageLimit = Math.min(INTERNAL_PAGE_SIZE, maxItems - rows.length)
    const upstream = await callInternalApi('/api/v1/prints', {
      params: { game, set_code: setCode, limit: pageLimit, offset },
      timeoutMs: 20000,
    })

    if (!upstream.ok) return { ok: false, upstream }

    const page = toItems(upstream.payload)
    rows.push(...page)
    if (page.length < pageLimit) break
    offset += page.length
    if (offset > 1000) break
  }

  return { ok: true, rows }
}

async function fetchSetSearchProfiles({ game, setCode, maxItems }) {
  const byPrintId = new Map()
  let offset = 0
  let total = null

  while (offset < maxItems && (total === null || offset < total)) {
    const pageLimit = Math.min(INTERNAL_PAGE_SIZE, maxItems - offset)
    const response = await callInternalApi('/api/v2/search/advanced', {
      method: 'POST',
      body: {
        game,
        q: '',
        filters: { set: setCode },
        limit: pageLimit,
        offset,
      },
      timeoutMs: 30000,
    })

    if (!response.ok) break

    const page = toItems(response.payload)
    total = Number(response.payload?.total ?? page.length)
    for (const item of page) {
      const printId = item?.print_id || item?.id
      if (printId) byPrintId.set(String(printId), item)
    }

    if (page.length < pageLimit) break
    offset += page.length
  }

  return byPrintId
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = searchParams.get('game') || ''
  const setCode = searchParams.get('set_code') || ''
  const limit = searchParams.get('limit') || 200
  const offset = searchParams.get('offset') || 0

  if (!setCode) {
    return NextResponse.json({ error: 'set_code_required', message: 'Missing set_code query param.' }, { status: 400 })
  }

  const [setUpstream, printsResult] = await Promise.all([
    callInternalApi('/api/v1/sets', {
      params: { game, q: setCode, limit: 50, offset: 0 },
    }),
    fetchAllPrints({ game, setCode, requestedLimit: limit, requestedOffset: offset }),
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

  if (!printsResult.ok) {
    const upstream = printsResult.upstream
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'catalog_set_prints_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  const sets = toItems(setUpstream.payload)
  const set = sets.find((item) => String(item?.code || '').toLowerCase() === String(setCode).toLowerCase()) || sets[0] || null
  const searchProfiles = await fetchSetSearchProfiles({
    game,
    setCode,
    maxItems: Math.min(Math.max(printsResult.rows.length, 1), MAX_SET_PRINTS),
  })
  const cards = printsResult.rows.map((item) => normalizePrint(item, searchProfiles.get(String(item.id))))

  return NextResponse.json({
    set: set
      ? {
          id: set.id,
          code: set.code,
          name: set.name,
          game_slug: set.game_slug || game,
          release_date: set.release_date || set.released_at || null,
          series: set.series || set.block || '',
          card_count: set.card_count ?? set.total_cards ?? null,
          print_count: cards.length,
          collector_total: cards.length,
        }
      : null,
    cards,
  })
}
