import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'
import { marketFromSearchItem } from '../../../../lib/searchV2/market'

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
  return NextResponse.json({
    ...payload,
    items: items.map((item) => ({ ...item, market: marketFromSearchItem(item) })),
  })
}
