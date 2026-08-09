import { NextResponse } from 'next/server'
import { callUserApi } from '../../../../lib/auth/serverSession'

function responseFor(upstream) {
  return NextResponse.json(
    upstream.payload || { error: 'library_request_failed' },
    { status: upstream.status || (upstream.ok ? 200 : 500) },
  )
}

export async function GET() {
  return responseFor(await callUserApi('/api/v2/me/collection'))
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  return responseFor(await callUserApi('/api/v2/me/collection', { method: 'POST', body }))
}

export async function DELETE(request) {
  const body = await request.json().catch(() => ({}))
  return responseFor(await callUserApi('/api/v2/me/collection', { method: 'DELETE', body }))
}
