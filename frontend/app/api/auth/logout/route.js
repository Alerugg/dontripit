import { NextResponse } from 'next/server'
import { callUserApi, clearSessionCookie } from '../../../../lib/auth/serverSession'

export async function POST() {
  await callUserApi('/api/v2/auth/logout', { method: 'POST' })
  const response = NextResponse.json({ ok: true })
  return clearSessionCookie(response)
}
