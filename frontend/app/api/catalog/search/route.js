import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'
import { callInternalApi, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

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
    return NextResponse.json(
      { error: 'catalog_search_failed', message: getPublicErrorMessage(upstream.status) },
      { status: upstream.status },
    )
  }

  return NextResponse.json({ items: Array.isArray(upstream.payload) ? upstream.payload : upstream.payload?.items || [] })
}
