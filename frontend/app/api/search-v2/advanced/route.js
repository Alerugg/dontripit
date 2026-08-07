import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

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

  return NextResponse.json(upstream.payload || { items: [], total: 0 })
}
