import { toApiGameSlug } from './games'

const RESPONSE_CACHE = new Map()
const FIVE_MINUTES = 5 * 60 * 1000

function toQuery(params = {}) {
  const search = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })

  const query = search.toString()
  return query ? `?${query}` : ''
}

async function request(path, params, { ttlMs = 0 } = {}) {
  const url = `${path}${toQuery(params)}`
  const now = Date.now()

  if (ttlMs > 0) {
    const cached = RESPONSE_CACHE.get(url)
    if (cached && cached.expiresAt > now) return cached.promise
  }

  const promise = fetch(url, {
    method: 'GET',
    cache: 'no-store',
  }).then(async (response) => {
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(
        payload?.message ||
        payload?.detail ||
        payload?.error ||
        'No pudimos cargar datos del catálogo.',
      )
    }
    return payload
  })

  if (ttlMs > 0) {
    RESPONSE_CACHE.set(url, { expiresAt: now + ttlMs, promise })
    promise.catch(() => RESPONSE_CACHE.delete(url))
  }

  return promise
}

export async function searchCatalog(filters = {}) {
  const payload = await request('/api/catalog/search', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
  })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function suggestCatalog(filters = {}) {
  const payload = await request('/api/catalog/suggest', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
  })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export function fetchCardById(id) {
  return request(`/api/catalog/cards/${id}`, {}, { ttlMs: FIVE_MINUTES })
}

export function fetchPrintById(id) {
  return request(`/api/catalog/prints/${id}`, {}, { ttlMs: FIVE_MINUTES })
}

export async function fetchGamePrints(filters = {}) {
  const payload = await request('/api/catalog/search', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
    type: 'print',
  })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function fetchSetsPage(game, options = {}) {
  const payload = await request('/api/catalog/sets', {
    game: toApiGameSlug(game || ''),
    limit: options.limit ?? 24,
    offset: options.offset ?? 0,
    q: options.q ?? '',
  }, { ttlMs: 10 * 60 * 1000 })

  if (Array.isArray(payload)) {
    return { items: payload, total: payload.length, limit: options.limit ?? 24, offset: options.offset ?? 0 }
  }
  return {
    items: payload?.items || [],
    total: Number(payload?.total ?? payload?.count ?? 0),
    limit: Number(payload?.limit ?? options.limit ?? 24),
    offset: Number(payload?.offset ?? options.offset ?? 0),
  }
}

export async function fetchSetsByGame(game, options = {}) {
  const payload = await fetchSetsPage(game, options)
  return payload.items
}

export function fetchSetDetail(game, setCode, options = {}) {
  return request('/api/catalog/set-detail', {
    game: toApiGameSlug(game || ''),
    set_code: setCode,
    q: options.q ?? '',
    sort: options.sort ?? 'number_asc',
    limit: options.limit ?? 36,
    offset: options.offset ?? 0,
  }, { ttlMs: FIVE_MINUTES })
}

export async function fetchNewsByGame(game, options = {}) {
  const payload = await request('/api/catalog/news', {
    game: toApiGameSlug(game || ''),
    limit: options.limit ?? 6,
  }, { ttlMs: FIVE_MINUTES })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function fetchReleasesByGame(game, options = {}) {
  const payload = await request('/api/catalog/releases', {
    game: toApiGameSlug(game || ''),
    region: options.region ?? '',
    upcoming: options.upcoming === false ? 0 : 1,
    limit: options.limit ?? 12,
  }, { ttlMs: FIVE_MINUTES })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function fetchMarketProductsByGame(game, options = {}) {
  const payload = await request('/api/catalog/market-products', {
    game: toApiGameSlug(game || ''),
    q: options.q ?? '',
    limit: options.limit ?? 24,
    offset: options.offset ?? 0,
  }, { ttlMs: 60 * 1000 })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export const RESULT_TYPE_OPTIONS = [
  { value: '', label: 'Todo' },
  { value: 'card', label: 'Cartas' },
  { value: 'print', label: 'Prints' },
  { value: 'set', label: 'Sets' },
]
