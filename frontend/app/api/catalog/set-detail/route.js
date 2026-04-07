import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

function normalizePrint(item = {}) {
  return {
    ...item,
    type: 'print',
    title: item.title || item.name || `Card #${item.card_id || item.id}`,
    set_name: item.set_name || item.set_code,
    variant_count: 1,
  }
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

  const [setUpstream, printsUpstream] = await Promise.all([
    callInternalApi('/api/v1/sets', {
      params: {
        game,
        q: setCode,
        limit: 50,
        offset: 0,
      },
    }),
    callInternalApi('/api/v1/prints', {
      params: {
        game,
        set_code: setCode,
        limit,
        offset,
      },
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

  if (!printsUpstream.ok) {
    const developerHint = getDeveloperErrorHint(printsUpstream.payload, printsUpstream.status)
    return NextResponse.json(
      {
        error: 'catalog_set_prints_failed',
        message: getPublicErrorMessage(printsUpstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: printsUpstream.status },
    )
  }

  const sets = toItems(setUpstream.payload)
  const set =
    sets.find((item) => String(item?.code || '').toLowerCase() === String(setCode).toLowerCase()) ||
    sets[0] ||
    null

  const cards = toItems(printsUpstream.payload).map(normalizePrint)

  return NextResponse.json({
    set: set
      ? {
          id: set.id,
          code: set.code,
          name: set.name,
          game_slug: set.game_slug || game,
          print_count: cards.length,
          collector_total: cards.length,
        }
      : null,
    cards,
  })
}
