import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

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
      { status: upstream.status },
    )
  }

  const payload = upstream.payload || {}
  const requestedCardId = String(params.id || '')
  const normalizedPrints = Array.isArray(payload?.prints)
    ? payload.prints.filter((print) => String(print?.card_id || '') === requestedCardId)
    : []
  const normalizedSets = Array.isArray(payload?.sets)
    ? payload.sets.filter((setItem) => normalizedPrints.some((print) => String(print?.set_code || '').toLowerCase() === String(setItem?.code || '').toLowerCase()))
    : []

  return NextResponse.json({
    ...payload,
    id: payload?.id ?? Number(params.id),
    primary_image_url: payload?.primary_image_url || normalizedPrints[0]?.primary_image_url || null,
    prints: normalizedPrints,
    sets: normalizedSets,
  })
}
