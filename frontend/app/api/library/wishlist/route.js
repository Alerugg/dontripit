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

async function preserveExistingWishlistFields(body = {}) {
  const printId = Number(body?.print_id)
  if (!Number.isFinite(printId) || printId <= 0) return body

  const current = await callUserApi('/api/v2/me/wishlist')
  if (!current.ok || !Array.isArray(current.payload?.items)) return body

  const existing = current.payload.items.find((item) => Number(item?.print?.id) === printId)
  if (!existing) return body

  return {
    ...body,
    print_id: printId,
    priority: hasOwn(body, 'priority') ? body.priority : existing.priority,
    target_price: hasOwn(body, 'target_price') ? body.target_price : existing.target_price,
    target_currency: hasOwn(body, 'target_currency') ? body.target_currency : existing.target_currency,
  }
}

export async function GET() {
  return responseFor(await callUserApi('/api/v2/me/wishlist'))
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  const safeBody = await preserveExistingWishlistFields(body)
  return responseFor(await callUserApi('/api/v2/me/wishlist', { method: 'POST', body: safeBody }))
}

export async function DELETE(request) {
  const body = await request.json().catch(() => ({}))
  return responseFor(await callUserApi('/api/v2/me/wishlist', { method: 'DELETE', body }))
}
