import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../../lib/catalog/internalApi'

export async function GET(_, { params }) {
  const { id } = await params
  const upstream = await callInternalApi(`/api/v1/prints/${id}/physical-releases`, { timeoutMs: 15000 })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'catalog_print_physical_releases_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { physical_releases: [], physical_release_names: [] })
}
