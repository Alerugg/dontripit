import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const upstream = await callInternalApi('/api/v2/search/don', {
    params: {
      q: searchParams.get('q') || '',
      game: 'onepiece',
      limit: searchParams.get('limit') || 24,
      offset: searchParams.get('offset') || 0,
    },
    timeoutMs: 15000,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'onepiece_don_search_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { items: [], total: 0, don_only: true })
}
