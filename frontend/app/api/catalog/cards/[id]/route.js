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
  const requestedCardId = String(params.id || '').trim()
  const requestedCardIdAsNumber = Number(requestedCardId)
  const payloadCardIdAsNumber = Number(payload?.id)
  const canCompareAsNumber = Number.isFinite(requestedCardIdAsNumber) && Number.isFinite(payloadCardIdAsNumber)

  if ((payload?.id !== undefined && payload?.id !== null) && (
    canCompareAsNumber
      ? payloadCardIdAsNumber !== requestedCardIdAsNumber
      : String(payload.id || '').trim() !== requestedCardId
  )) {
    return NextResponse.json(
      {
        error: 'catalog_card_not_found',
        message: 'No encontramos la carta solicitada.',
      },
      { status: 404 },
    )
  }

  const normalizedPrints = Array.isArray(payload?.prints)
    ? payload.prints.filter((print) => {
      const printCardId = String(print?.card_id ?? '').trim()
      const printCardIdAsNumber = Number(printCardId)
      if (canCompareAsNumber && Number.isFinite(printCardIdAsNumber)) {
        return printCardIdAsNumber === requestedCardIdAsNumber
      }
      return printCardId === requestedCardId
    })
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
