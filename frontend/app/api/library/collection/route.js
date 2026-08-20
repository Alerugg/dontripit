import { NextResponse } from 'next/server'
import { callUserApi } from '../../../../lib/auth/serverSession'

function responseFor(upstream) {
  return NextResponse.json(
    upstream.payload || { error: 'library_request_failed' },
    { status: upstream.status || (upstream.ok ? 200 : 500) },
  )
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key)
}

async function preserveExistingCollectionFields(body = {}) {
  const printId = Number(body?.print_id)
  if (!Number.isFinite(printId) || printId <= 0) return body

  const current = await callUserApi('/api/v2/me/collection')
  if (!current.ok || !Array.isArray(current.payload?.items)) return body

  const existing = current.payload.items.find((item) => Number(item?.print?.id) === printId)
  if (!existing) return body

  return {
    ...body,
    print_id: printId,
    quantity: hasOwn(body, 'quantity') ? body.quantity : existing.quantity,
    condition: hasOwn(body, 'condition') ? body.condition : existing.condition,
    notes: hasOwn(body, 'notes') ? body.notes : existing.notes,
    purchase_price: hasOwn(body, 'purchase_price') ? body.purchase_price : existing.purchase_price,
    purchase_currency: hasOwn(body, 'purchase_currency') ? body.purchase_currency : existing.purchase_currency,
    acquired_at: hasOwn(body, 'acquired_at') ? body.acquired_at : existing.acquired_at,
  }
}

export async function GET() {
  return responseFor(await callUserApi('/api/v2/me/collection'))
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  const safeBody = await preserveExistingCollectionFields(body)
  return responseFor(await callUserApi('/api/v2/me/collection', { method: 'POST', body: safeBody }))
}

export async function DELETE(request) {
  const body = await request.json().catch(() => ({}))
  return responseFor(await callUserApi('/api/v2/me/collection', { method: 'DELETE', body }))
}
