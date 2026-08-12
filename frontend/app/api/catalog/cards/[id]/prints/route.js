import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../../lib/catalog/internalApi'

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(parsed, minimum), maximum)
}

export async function GET(request, { params }) {
  const { id } = await params
  const { searchParams } = new URL(request.url)
  const limit = boundedInt(searchParams.get('limit'), 24, 1, 50)
  const offset = boundedInt(searchParams.get('offset'), 0, 0, 100000)
  const upstream = await callInternalApi(`/api/v1/cards/${id}/prints`, {
    params: { limit, offset },
    timeoutMs: 20000,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'catalog_card_prints_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { items: [], total: 0, limit, offset })
}
