import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const q = String(searchParams.get('q') || '').trim()
  if (!q) return NextResponse.json({ items: [], don_only: true })

  const upstream = await callInternalApi('/api/v2/search/don/suggest', {
    params: {
      q,
      game: 'onepiece',
      limit: searchParams.get('limit') || 8,
    },
    timeoutMs: 10000,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'onepiece_don_suggest_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  const payload = upstream.payload || { items: [] }
  const items = (payload.items || []).map((item) => ({
    ...item,
    title: item.name,
    primary_image_url: item.primary_image_url || item.image_url || null,
  }))
  return NextResponse.json({ ...payload, items })
}
