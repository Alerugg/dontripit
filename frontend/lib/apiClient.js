import { readStoredApiKey } from './apiKeyStorage'

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || '').replace(/\/$/, '')
const API_KEY = (process.env.NEXT_PUBLIC_API_KEY || '').trim()

function makeUrl(path, params = {}) {
  const cleanPath = path.startsWith('/') ? path : `/${path}`
  const base = API_BASE_URL || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000')
  const url = new URL(`${base}${cleanPath}`)

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    url.searchParams.set(key, String(value))
  })

  return API_BASE_URL ? url.toString() : `${url.pathname}${url.search}`
}

function getActiveApiKey() {
  if (API_KEY) return API_KEY
  return readStoredApiKey().trim()
}

function buildFriendlyError(payload, status) {
  const detail = payload?.detail || payload?.error || ''
  if (String(detail).includes('missing_api_key')) {
    return 'Falta API key. Configúrala en API Console o en NEXT_PUBLIC_API_KEY para desarrollo local.'
  }
  if (status === 401 || status === 403) {
    return 'No autorizado. Verifica que tu API key sea válida para este entorno.'
  }
  return detail || `Request failed (${status})`
}

async function request(path, { params, ...options } = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }

  const runtimeApiKey = getActiveApiKey()
  if (runtimeApiKey) {
    headers['X-API-Key'] = runtimeApiKey
  }

  const response = await fetch(makeUrl(path, params), {
    ...options,
    headers,
    cache: 'no-store',
  })

  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(buildFriendlyError(payload, response.status))
  }

  return payload
}

export function searchCatalog({ q, game, type, limit = 30, offset = 0 }) {
  return request('/api/v1/search', {
    params: { q, game, type, limit, offset },
  })
}

export function fetchCardById(id) {
  return request(`/api/v1/cards/${id}`)
}

export function fetchPrintById(id) {
  return request(`/api/v1/prints/${id}`)
}

export function fetchGames() {
  return request('/api/v1/games')
}

export function fetchApi(path, params = {}) {
  return request(path, { params })
}

export function getApiBaseUrlLabel() {
  return API_BASE_URL || 'same-origin'
}

export const fetchCardDetail = fetchCardById
export const fetchPrintDetail = fetchPrintById
