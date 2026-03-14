import { NextResponse } from 'next/server'
import { callInternalApi, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

export async function GET(_, { params }) {
  const upstream = await callInternalApi(`/api/v1/cards/${params.id}`)

  if (!upstream.ok) {
    return NextResponse.json(
      { error: 'catalog_card_failed', message: getPublicErrorMessage(upstream.status) },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload)
}
