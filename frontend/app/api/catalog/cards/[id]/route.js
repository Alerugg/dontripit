import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'
import { callInternalApi, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

export async function GET(_, { params }) {
  const upstream = await callInternalApi(`/api/v1/cards/${params.id}`)

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'catalog_card_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
    return NextResponse.json(
      { error: 'catalog_card_failed', message: getPublicErrorMessage(upstream.status) },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload)
}
