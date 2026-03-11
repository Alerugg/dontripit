const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || ''
const DEFAULT_API_KEY = process.env.NEXT_PUBLIC_API_KEY || ''

function normalizeBaseUrl(baseUrl) {
  if (!baseUrl) return ''
  return baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl
}

function buildUrl(path, params = {}) {
  const base = normalizeBaseUrl(API_BASE_URL)
  const prefix = path.startsWith('/') ? path : `/${path}`
  const url = new URL(`${base}${prefix}`, typeof window !== 'undefined' ? window.location.origin : 'http://localhost')

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    url.searchParams.set(key, String(value))
  })

  if (!base) return `${url.pathname}${url.search}`
  return `${url.origin}${url.pathname}${url.search}`
}

function buildHeaders(apiKey, extraHeaders = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...extraHeaders,
  }
  const key = (apiKey || DEFAULT_API_KEY || '').trim()
  if (key) headers['X-API-Key'] = key
  return headers
}

export async function apiRequest(path, { params, apiKey, headers, ...options } = {}) {
  const response = await fetch(buildUrl(path, params), {
    ...options,
    headers: buildHeaders(apiKey, headers),
    cache: 'no-store',
  })

  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    const message = payload?.detail || payload?.error || `Request failed (${response.status})`
    throw new Error(message)
  }

  return payload
}

export async function generateDevApiKey(adminToken) {
  return apiRequest('/api/admin/dev/api-keys', {
    method: 'POST',
    headers: {
      'X-Admin-Token': (adminToken || '').trim(),
    },
  })
}

export async function fetchGames(apiKey) {
  return apiRequest('/api/v1/games', { apiKey })
}

export async function fetchSearch(params, apiKey) {
  return apiRequest('/api/v1/search', { params, apiKey })
}

export async function fetchSuggest(params, apiKey) {
  return apiRequest('/api/v1/search/suggest', { params, apiKey })
}

export async function fetchCardDetail(cardId, apiKey) {
  return apiRequest(`/api/v1/cards/${cardId}`, { apiKey })
}

export async function fetchPrintDetail(printId, apiKey) {
  return apiRequest(`/api/v1/prints/${printId}`, { apiKey })
}

export function getApiRuntimeConfig() {
  return {
    baseUrl: API_BASE_URL || 'same-origin (/api/* via rewrite)',
    hasDefaultApiKey: Boolean(DEFAULT_API_KEY),
  }
}
