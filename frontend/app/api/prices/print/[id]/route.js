import { NextResponse } from 'next/server'
import { callInternalApi } from '../../../../../lib/catalog/internalApi'

export async function GET(_request, { params }) {
  const { id } = await params
  const upstream = await callInternalApi('/api/v1/prices', {
    params: {
      entity_type: 'print',
      entity_id: id,
      source: 'cardmarket',
      currency: 'EUR',
      granularity: 'raw',
    },
  })
  if (!upstream.ok) {
    return NextResponse.json({ price: null }, { status: upstream.status || 500 })
  }

  const series = Array.isArray(upstream.payload?.series) ? upstream.payload.series : []
  const latest = series.length ? series[series.length - 1] : null
  if (!latest) return NextResponse.json({ price: null })

  const conservative = latest.mid ?? null
  const minimum = latest.low ?? null
  const trend = latest.market ?? null
  const average = latest.last ?? null
  const value = conservative ?? trend ?? average ?? minimum

  return NextResponse.json({
    price: value === null ? null : {
      value,
      minimum,
      conservative,
      trend,
      average,
      currency: latest.currency || 'EUR',
      source: latest.source || 'cardmarket',
      as_of: latest.as_of || null,
      finish: latest.finish || null,
      valuation_method: conservative !== null ? 'cardmarket_low_ex_plus_or_foil_low' : null,
    },
  })
}
