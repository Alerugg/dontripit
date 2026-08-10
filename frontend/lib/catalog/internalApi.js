const DEFAULT_TIMEOUT_MS = 12000
const MAX_TIMEOUT_MS = 30000
const PRODUCTION_API_BASE_URL = 'https://api.dontripit.com'
const LEGACY_BACKEND_HOSTS = new Set(['dontripit-production.up.railway.app'])

function normalizeInternalBaseUrl(value) {
  const raw = String(value || '').trim().replace(/\/$/, '')
  if (!raw) return ''

  try {
    const parsed = new URL(raw)
    if (LEGACY_BACKEND_HOSTS.has(parsed.hostname.toLowerCase())) return PRODUCTION_API_BASE_URL
    return raw
  } catch {
    return raw
  }
}

function getInternalConfig() {
  const baseUrl = normalizeInternalBaseUrl(process.env.INTERNAL_API_BASE_URL)
    || (process.env.VERCEL_ENV === 'production' ? PRODUCTION_API_BASE_URL : '')
  const apiKey = (process.env.INTERNAL_API_KEY || '').trim()
  const allowPublic = String(process.env.INTERNAL_API_ALLOW_PUBLIC || '').trim().toLowerCase() === 'true'

  if (!baseUrl) {
    return {
      ok: false,
      reason: 'missing_internal_api_base_url',
      hint: 'Define INTERNAL_API_BASE_URL (ej: http://backend:5000 en Docker).',
    }
  }

  if (!apiKey && !allowPublic) {
    return {
      ok: false,
      reason: 'missing_internal_api_key',
      hint: 'Define INTERNAL_API_KEY en el entorno del frontend (misma clave aceptada por backend).',
    }
  }

  return { ok: true, baseUrl, apiKey, allowPublic }
}

async function _fetchInternal(url, { method, body, apiKey, signal, headers = {} }) {
  const response = await fetch(url.toString(), {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...headers,
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
    signal,
  })

  const payload = await response.json().catch(() => null)
  return { response, payload }
}

export async function callInternalApi(path, {
  method = 'GET',
  params = {},
  body,
  headers = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  const config = getInternalConfig()
  if (!config.ok) {
    return {
      ok: false,
      status: 503,
      payload: {
        error: config.reason,
        hint: config.hint,
      },
    }
  }

  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const url = new URL(`${config.baseUrl}${normalizedPath}`)

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    url.searchParams.set(key, String(value))
  })

  const requestedTimeout = Number(timeoutMs)
  const resolvedTimeout = Number.isFinite(requestedTimeout)
    ? Math.min(MAX_TIMEOUT_MS, Math.max(1000, requestedTimeout))
    : DEFAULT_TIMEOUT_MS
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), resolvedTimeout)

  try {
    const { response, payload } = await _fetchInternal(url, {
      method,
      body,
      apiKey: config.apiKey,
      signal: controller.signal,
      headers,
    })

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        payload,
      }
    }

    return {
      ok: true,
      status: response.status,
      payload,
    }
  } catch (error) {
    if (error?.name === 'AbortError') {
      return { ok: false, status: 504, payload: { error: 'upstream_timeout' } }
    }

    return { ok: false, status: 502, payload: { error: 'upstream_unreachable' } }
  } finally {
    clearTimeout(timeout)
  }
}

export function getPublicErrorMessage(status) {
  if (status === 404) return 'No encontramos el recurso solicitado.'
  if (status === 503) return 'El catálogo no está configurado todavía en este entorno.'
  if (status === 504) return 'La API tardó demasiado. Inténtalo de nuevo.'
  if (status >= 500) return 'No pudimos cargar datos del catálogo en este momento.'
  return 'No pudimos procesar tu solicitud de catálogo.'
}

export function getDeveloperErrorHint(upstreamPayload = {}, status) {
  if (status === 503) {
    return upstreamPayload?.hint || 'Revisa INTERNAL_API_BASE_URL e INTERNAL_API_KEY en el entorno del frontend.'
  }

  if (status === 502) {
    return 'No hay conexión al backend. Verifica red y que el backend esté saludable.'
  }

  if (status === 401 || status === 403) {
    return 'El backend rechazó la credencial. Revisa INTERNAL_API_KEY en el entorno del frontend.'
  }

  return undefined
}
