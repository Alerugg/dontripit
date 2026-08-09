import { NextResponse } from 'next/server'
import { callInternalApi } from '../../../../lib/catalog/internalApi'
import { setSessionCookie } from '../../../../lib/auth/serverSession'

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  const upstream = await callInternalApi('/api/v2/auth/login', { method: 'POST', body })
  if (!upstream.ok) {
    return NextResponse.json(
      upstream.payload || { error: 'login_failed', message: 'No pudimos iniciar sesión.' },
      { status: upstream.status || 500 },
    )
  }

  const response = NextResponse.json({ user: upstream.payload?.user })
  return setSessionCookie(response, upstream.payload?.session_token, upstream.payload?.expires_at)
}
