import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

const MIN_SUGGEST_QUERY_LENGTH = 2

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const q = String(searchParams.get('q') || '').trim()
  if (q.length < MIN_SUGGEST_QUERY_LENGTH) {
    return NextResponse.json({ items: [] })
  }

  const upstream = await callInternalApi('/api/v2/search/suggest', {
    params: {
      q,
      game: searchParams.get('game') || '',
      limit: searchParams.get('limit') || 8,
    },
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'search_v2_suggest_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { items: [] })
}