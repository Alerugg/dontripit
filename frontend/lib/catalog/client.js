import { toApiGameSlug } from './games'

function toQuery(params = {}) {
  const search = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })

  const query = search.toString()
  return query ? `?${query}` : ''
}

async function request(path, params) {
  const response = await fetch(`${path}${toQuery(params)}`, {
    method: 'GET',
    cache: 'no-store',
  })

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
  return request(`/api/catalog/cards/${id}`)
}

export function fetchPrintById(id) {
  return request(`/api/catalog/prints/${id}`)
}

export async function fetchGamePrints(filters = {}) {
  const payload = await request('/api/catalog/search', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
    type: 'print',
  })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function fetchSetsByGame(game, options = {}) {
  const payload = await request('/api/catalog/sets', {
    game: toApiGameSlug(game || ''),
    limit: options.limit ?? 500,
    offset: options.offset ?? 0,
    q: options.q ?? '',
  })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export function fetchSetDetail(game, setCode, options = {}) {
  return request('/api/catalog/set-detail', {
    game: toApiGameSlug(game || ''),
    set_code: setCode,
    limit: options.limit ?? 200,
    offset: options.offset ?? 0,
  })
}

export async function fetchNewsByGame(game, options = {}) {
  const payload = await request('/api/catalog/news', {
    game: toApiGameSlug(game || ''),
    limit: options.limit ?? 6,
  })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export const RESULT_TYPE_OPTIONS = [
  { value: '', label: 'Todo' },
  { value: 'card', label: 'Cartas' },
  { value: 'print', label: 'Prints' },
  { value: 'set', label: 'Sets' },
]