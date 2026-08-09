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
