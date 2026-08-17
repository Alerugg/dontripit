import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const MAX_PAGE_SIZE = 50
const PUBLIC_CACHE_HEADERS = { 'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300' }

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
}

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

function normalizePrint(item = {}) {
  const cardName = String(item.card_name || item.name || '').trim() || null
  const exactVariant = item.variant && item.variant !== 'default' ? item.variant : null
  const physicalReleases = Array.isArray(item.physical_releases) ? item.physical_releases : []
  return {
    ...item,
    id: item.print_id || item.id,
    print_id: item.print_id || item.id,
    type: 'print',
    name: cardName,
    title: cardName,
    variant: exactVariant,
    exact_variant: exactVariant,
    primary_image_url: item.primary_image_url || item.image_url,
    variant_count: 1,
    finish: item.is_foil ? 'foil' : 'non-foil',
    physical_releases: physicalReleases,
    physical_release_names: Array.isArray(item.physical_release_names)
      ? item.physical_release_names
      : physicalReleases.map((release) => release?.name).filter(Boolean),
  }
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = searchParams.get('game') || ''
  const setCode = searchParams.get('set_code') || ''
  const q = searchParams.get('q') || ''
  const sort = searchParams.get('sort') || 'number_asc'
  const limit = boundedInt(searchParams.get('limit'), 36, 1, MAX_PAGE_SIZE)
  const offset = boundedInt(searchParams.get('offset'), 0, 0, 100000)

  if (!setCode) {
    return NextResponse.json({ error: 'set_code_required', message: 'Missing set_code query param.' }, { status: 400 })
  }

  const [setUpstream, checklistUpstream] = await Promise.all([
    callInternalApi('/api/v1/sets', {
      params: { game, q: setCode, limit: 50, offset: 0 },
    }),
    callInternalApi('/api/v1/set-ui/prints', {
      params: { game, set_code: setCode, q, sort, limit, offset },
      timeoutMs: 20000,
    }),
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

  if (!checklistUpstream.ok) {
    const developerHint = getDeveloperErrorHint(checklistUpstream.payload, checklistUpstream.status)
    return NextResponse.json(
      {
        error: 'catalog_set_prints_failed',
        message: getPublicErrorMessage(checklistUpstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: checklistUpstream.status },
    )
  }

  const sets = toItems(setUpstream.payload)
  const set = sets.find((item) => String(item?.code || '').toLowerCase() === String(setCode).toLowerCase()) || sets[0] || null
  const cards = toItems(checklistUpstream.payload).map(normalizePrint)
  const total = Number(checklistUpstream.payload?.total ?? cards.length)

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
          print_count: total,
          collector_total: Number(checklistUpstream.payload?.unfiltered_total ?? total),
        }
      : null,
    cards,
    total,
    limit: Number(checklistUpstream.payload?.limit ?? limit),
    offset: Number(checklistUpstream.payload?.offset ?? offset),
    scope: checklistUpstream.payload?.scope || null,
  }, { headers: PUBLIC_CACHE_HEADERS })
}
