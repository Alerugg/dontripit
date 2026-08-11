import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

function toItems(payload) {
  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  const upstream = await callInternalApi('/api/v2/search/advanced', {
    method: 'POST',
    body,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: upstream.payload?.error || 'search_v2_advanced_failed',
        message: upstream.payload?.detail || getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  const payload = upstream.payload || { items: [], total: 0 }
  const items = toItems(payload)
  const ids = items.map((item) => item.print_id || item.id).filter(Boolean)
  if (!ids.length) return NextResponse.json(payload)

  const marketUpstream = await callInternalApi('/api/v1/market/prints/cardmarket-batch', {
    params: { ids: ids.join(',') },
    timeoutMs: 15000,
  })
  if (!marketUpstream.ok) return NextResponse.json(payload)

  const marketByPrint = new Map(
    toItems(marketUpstream.payload).map((item) => [String(item.print_id), item]),
  )
  return NextResponse.json({
    ...payload,
    items: items.map((item) => ({
      ...item,
      market: marketByPrint.get(String(item.print_id || item.id)) || null,
    })),
  })
}
