import { cookies } from 'next/headers'
import { callInternalApi } from '../catalog/internalApi'

export const SESSION_COOKIE = 'dri_session'

export async function getSessionToken() {
  const store = await cookies()
  return store.get(SESSION_COOKIE)?.value || ''
}

export async function callUserApi(path, options = {}) {
  const token = await getSessionToken()
  if (!token) {
    return { ok: false, status: 401, payload: { error: 'authentication_required' } }
  }
  return callInternalApi(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${token}`,
    },
  })
}

export function setSessionCookie(response, token, expiresAt) {
  if (!token) return response
  response.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    expires: expiresAt ? new Date(expiresAt) : undefined,
  })
  return response
}

export function clearSessionCookie(response) {
  response.cookies.set(SESSION_COOKIE, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    expires: new Date(0),
  })
  return response
}
