import { NextResponse } from 'next/server'
import { callInternalApi } from '../../../../lib/catalog/internalApi'
import { setSessionCookie } from '../../../../lib/auth/serverSession'

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  const upstream = await callInternalApi('/api/v2/auth/register', { method: 'POST', body })
  if (!upstream.ok) {
    return NextResponse.json(
      upstream.payload || { error: 'registration_failed', message: 'No pudimos crear tu cuenta.' },
      { status: upstream.status || 500 },
    )
  }

  const response = NextResponse.json({ user: upstream.payload?.user }, { status: 201 })
  return setSessionCookie(response, upstream.payload?.session_token, upstream.payload?.expires_at)
}
