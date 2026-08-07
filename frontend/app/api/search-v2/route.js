import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const upstream = await callInternalApi('/api/v2/search', {
    params: {
      q: searchParams.get('q') || '',
      game: searchParams.get('game') || '',
      limit: searchParams.get('limit') || 24,
    },
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'search_v2_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { items: [], count: 0 })
}
