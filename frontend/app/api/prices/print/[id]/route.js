import { NextResponse } from 'next/server'
import { callInternalApi } from '../../../../../lib/catalog/internalApi'

export async function GET(_request, { params }) {
  const { id } = await params
  const upstream = await callInternalApi(`/api/v1/market/prints/${id}/cardmarket`)

  if (!upstream.ok) {
    return NextResponse.json({ price: null, market_status: 'unavailable' }, { status: upstream.status || 500 })
  }

  const payload = upstream.payload || {}
  const price = payload.price || null
  const reference = payload.reference || null

  if (!price) {
    return NextResponse.json({
      price: null,
      market_status: payload.status || 'unpriced',
      reason: payload.reason || null,
      cardmarket: reference,
    })
  }

  return NextResponse.json({
    price: {
      ...price,
      cardmarket: reference,
      valuation_method: price.conservative !== null && price.conservative !== undefined
        ? 'cardmarket_low_ex_plus_or_foil_low'
        : null,
    },
    market_status: payload.status || 'priced',
    reason: payload.reason || null,
    cardmarket: reference,
  })
}
