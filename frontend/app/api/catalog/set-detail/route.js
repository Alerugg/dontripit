import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)

  const upstream = await callInternalApi('/api/v1/set-detail', {
    params: {
      game: searchParams.get('game') || '',
      set_code: searchParams.get('set_code') || '',
      limit: searchParams.get('limit') || 1000,
      offset: searchParams.get('offset') || 0,
    },
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)

    return NextResponse.json(
      {
        error: 'catalog_set_detail_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload)
}