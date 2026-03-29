import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)

  const upstream = await callInternalApi('/api/v1/sets', {
    params: {
      game: searchParams.get('game') || '',
      q: searchParams.get('q') || '',
      limit: searchParams.get('limit') || 24,
      offset: searchParams.get('offset') || 0,
    },
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)

    return NextResponse.json(
      {
        error: 'catalog_sets_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json({
    items: Array.isArray(upstream.payload) ? upstream.payload : upstream.payload?.items || [],
  })
}