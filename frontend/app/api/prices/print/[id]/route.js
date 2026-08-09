import { NextResponse } from 'next/server'
import { callInternalApi } from '../../../../../lib/catalog/internalApi'

export async function GET(_request, { params }) {
  const { id } = await params
  const upstream = await callInternalApi('/api/v1/prices', {
    params: { entity_type: 'print', entity_id: id },
  })
  if (!upstream.ok) {
    return NextResponse.json({ price: null }, { status: upstream.status || 500 })
  }

  const series = Array.isArray(upstream.payload?.series) ? upstream.payload.series : []
  const latest = series.length ? series[series.length - 1] : null
  if (!latest) return NextResponse.json({ price: null })
  const value = latest.close ?? latest.market ?? latest.last ?? null
  return NextResponse.json({
    price: value === null ? null : {
      value,
      currency: latest.currency || null,
      source: latest.source || null,
      as_of: latest.as_of || null,
      kind: latest.close !== undefined ? 'close' : latest.market !== undefined ? 'market' : 'last',
    },
  })
}
