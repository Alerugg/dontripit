import { NextResponse } from 'next/server'
import { callUserApi, clearSessionCookie } from '../../../../lib/auth/serverSession'

export async function GET() {
  const upstream = await callUserApi('/api/v2/auth/me')
  if (!upstream.ok) {
    const response = NextResponse.json(
      upstream.payload || { error: 'authentication_required' },
      { status: upstream.status || 401 },
    )
    if (upstream.status === 401) clearSessionCookie(response)
    return response
  }
  return NextResponse.json(upstream.payload)
}

export async function DELETE(request) {
  const body = await request.json().catch(() => ({}))
  const upstream = await callUserApi('/api/v2/auth/account', {
    method: 'DELETE',
    body,
    timeoutMs: 30000,
  })

  if (!upstream.ok) {
    const response = NextResponse.json(
      upstream.payload || { error: 'account_delete_failed', message: 'No pudimos eliminar la cuenta.' },
      { status: upstream.status || 500 },
    )
    if (upstream.status === 401 && upstream.payload?.error === 'authentication_required') clearSessionCookie(response)
    return response
  }

  const response = NextResponse.json(upstream.payload || { ok: true })
  clearSessionCookie(response)
  return response
}
