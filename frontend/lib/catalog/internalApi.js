const DEFAULT_TIMEOUT_MS = 12000

function getInternalConfig() {
  const baseUrl = (process.env.INTERNAL_API_BASE_URL || '').replace(/\/$/, '')
  const apiKey = (process.env.INTERNAL_API_KEY || '').trim()

  if (!baseUrl) {
    return {
      ok: false,
      reason: 'missing_internal_api_base_url',
      hint: 'Define INTERNAL_API_BASE_URL (ej: http://backend:5000 en Docker).',
    }
  }

  return { ok: true, baseUrl, apiKey }
}

async function _fetchInternal(url, { method, body, apiKey, signal }) {
  const response = await fetch(url.toString(), {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
    signal,
  })

  const payload = await response.json().catch(() => null)
  return { response, payload }
}

export async function callInternalApi(path, { method = 'GET', params = {}, body } = {}) {
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

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)

  try {
    let { response, payload } = await _fetchInternal(url, {
      method,
      body,
      apiKey: config.apiKey,
      signal: controller.signal,
    })

    if (!response.ok && config.apiKey && (response.status === 401 || response.status === 403)) {
      ;({ response, payload } = await _fetchInternal(url, {
        method,
        body,
        apiKey: '',
        signal: controller.signal,
      }))
    }

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
    return 'No hay conexión al backend. Verifica red Docker y que backend esté saludable.'
  }

  return undefined
}
