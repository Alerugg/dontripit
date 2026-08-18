import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../../lib/catalog/internalApi'

export async function GET(_request, { params }) {
  const { id } = await params
  const upstream = await callInternalApi(`/api/v1/cards/${id}/versions`, {
    timeoutMs: 20000,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'catalog_card_versions_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { versions: [], languages: [], version_count: 0, print_count: 0 })
}
