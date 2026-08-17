import { NextResponse } from 'next/server'
import { callInternalApi } from '../../../../lib/catalog/internalApi'

export async function POST(request) {
  const body = await request.json().catch(() => ({}))
  const upstream = await callInternalApi('/api/v2/auth/reset-password', {
    method: 'POST',
    body,
    timeoutMs: 30000,
  })
  return NextResponse.json(
    upstream.payload || { error: 'password_reset_failed', message: 'No pudimos cambiar la contraseña.' },
    { status: upstream.status || 500 },
  )
}
