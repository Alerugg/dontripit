import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)

  const upstream = await callInternalApi('/api/v1/search', {
    params: {
      q: searchParams.get('q') || '',
      game: searchParams.get('game') || '',
      type: searchParams.get('type') || '',
      limit: searchParams.get('limit') || 30,
      offset: searchParams.get('offset') || 0,
    },
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)

    return NextResponse.json(
      {
        error: 'catalog_search_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  const items = Array.isArray(upstream.payload) ? upstream.payload : upstream.payload?.items || []
  const printIds = items
    .filter((item) => item?.type === 'print' && Number.isInteger(Number(item?.id)))
    .map((item) => Number(item.id))
    .slice(0, 100)

  if (!printIds.length) {
    return NextResponse.json({ items })
  }

  // Market enrichment is deliberately read-only and non-blocking. The upstream
  // endpoint returns only prices already projected through accepted exact
  // Cardmarket links; if it is unavailable, catalog search remains usable and
  // we never substitute a price from a different physical printing.
  const market = await callInternalApi('/api/v1/market/prints/summary', {
    params: { ids: printIds.join(',') },
  })

  if (!market.ok) {
    return NextResponse.json({ items })
  }

  const marketByPrint = new Map(
    (Array.isArray(market.payload?.items) ? market.payload.items : []).map((row) => [Number(row.print_id), row]),
  )

  return NextResponse.json({
    items: items.map((item) => {
      if (item?.type !== 'print') return item
      const price = marketByPrint.get(Number(item.id))
      return price ? { ...item, market: price } : item
    }),
  })
}
