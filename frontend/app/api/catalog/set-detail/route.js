import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const INTERNAL_PAGE_SIZE = 50
const MAX_SET_PRINTS = 1000

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

function normalizePrint(item = {}) {
  const cardName = item.card_name || item.name || ''
  const exactVariant = item.variant && item.variant !== 'default' ? item.variant : null
  return {
    ...item,
    id: item.print_id || item.id,
    print_id: item.print_id || item.id,
    type: 'print',
    name: cardName || `Carta #${item.collector_number || item.card_id || item.id}`,
    title: cardName || `Carta #${item.collector_number || item.card_id || item.id}`,
    variant: exactVariant,
    exact_variant: exactVariant,
    primary_image_url: item.primary_image_url || item.image_url,
    variant_count: 1,
    finish: item.is_foil ? 'foil' : 'non-foil',
  }
}

async function fetchCompleteChecklist({ game, setCode, requestedLimit, requestedOffset }) {
  const rows = []
  const maxItems = Math.min(Math.max(Number(requestedLimit) || 200, 1), MAX_SET_PRINTS)
  let offset = Math.max(Number(requestedOffset) || 0, 0)
  let total = null

  while (rows.length < maxItems && (total === null || offset < total)) {
    const pageLimit = Math.min(INTERNAL_PAGE_SIZE, maxItems - rows.length)
    const upstream = await callInternalApi('/api/v1/set-ui/prints', {
      params: { game, set_code: setCode, limit: pageLimit, offset },
      timeoutMs: 20000,
    })

    if (!upstream.ok) return { ok: false, upstream }

    const page = toItems(upstream.payload)
    total = Number(upstream.payload?.total ?? page.length)
    rows.push(...page)
    if (page.length < pageLimit) break
    offset += page.length
    if (offset > 1000) break
  }

  return { ok: true, rows, total: total ?? rows.length }
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

  const [setUpstream, checklistResult] = await Promise.all([
    callInternalApi('/api/v1/sets', {
      params: { game, q: setCode, limit: 50, offset: 0 },
    }),
    fetchCompleteChecklist({ game, setCode, requestedLimit: limit, requestedOffset: offset }),
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

  if (!checklistResult.ok) {
    const upstream = checklistResult.upstream
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
  const cards = checklistResult.rows.map(normalizePrint)

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
          print_count: checklistResult.total,
          collector_total: checklistResult.total,
        }
      : null,
    cards,
  })
}
