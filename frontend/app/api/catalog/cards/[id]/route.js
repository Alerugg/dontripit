import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

function collectorRank(value = '') {
  const normalized = String(value || '').trim()
  if (!normalized) return Number.MAX_SAFE_INTEGER
  const trailing = normalized.match(/(\d+)$/)
  if (trailing) return Number(trailing[1])
  const first = normalized.match(/\d+/)
  return first ? Number(first[0]) : Number.MAX_SAFE_INTEGER
}

function comparePrints(a = {}, b = {}) {
  const collectorDelta = collectorRank(a.collector_number) - collectorRank(b.collector_number)
  if (collectorDelta !== 0) return collectorDelta

  const rawCollectorDelta = String(a.collector_number || '').localeCompare(String(b.collector_number || ''), undefined, {
    numeric: true,
    sensitivity: 'base',
  })
  if (rawCollectorDelta !== 0) return rawCollectorDelta

  const setDelta = String(a.set_code || '').localeCompare(String(b.set_code || ''), undefined, { sensitivity: 'base' })
  if (setDelta !== 0) return setDelta

  const finishDelta = String(a.finish || '').localeCompare(String(b.finish || ''), undefined, { sensitivity: 'base' })
  if (finishDelta !== 0) return finishDelta

  const variantDelta = String(a.variant || '').localeCompare(String(b.variant || ''), undefined, { sensitivity: 'base' })
  if (variantDelta !== 0) return variantDelta

  return String(a.id || '').localeCompare(String(b.id || ''), undefined, { numeric: true, sensitivity: 'base' })
}

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
    }).sort(comparePrints)
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
