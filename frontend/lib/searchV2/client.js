import { toApiGameSlug } from '../catalog/games'

const MIN_SUGGEST_QUERY_LENGTH = 2

function toQuery(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

function requestInit(options = {}, init = {}) {
  return {
    ...init,
    cache: 'no-store',
    signal: options.signal,
  }
}

async function readJson(response) {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.message || payload?.detail || payload?.error || 'Search V2 request failed.')
  }
  return payload
}

export async function searchV2({ q, game, limit = 24, language = '' } = {}, options = {}) {
  const response = await fetch(
    `/api/search-v2${toQuery({ q, game: toApiGameSlug(game || ''), limit, language })}`,
    requestInit(options, { method: 'GET' }),
  )
  const payload = await readJson(response)
  return payload?.items || []
}

export async function federatedSearchV2({ q, game, page = 1, limit = 24, kind = 'all', category = '', region = '', sort = 'relevance', hasPrice = false, language = '' } = {}, options = {}) {
  const response = await fetch(`/api/search-v2/federated${toQuery({
    q,
    game: toApiGameSlug(game || ''),
    page,
    limit,
    kind,
    category,
    region,
    sort,
    language,
    has_price: hasPrice ? 1 : '',
  })}`, requestInit(options, { method: 'GET' }))
  return readJson(response)
}

export async function suggestV2({ q, game, limit = 8, language = '' } = {}, options = {}) {
  const cleanQuery = String(q || '').trim()
  if (cleanQuery.length < MIN_SUGGEST_QUERY_LENGTH) return []

  const response = await fetch(
    `/api/search-v2/suggest${toQuery({ q: cleanQuery, game: toApiGameSlug(game || ''), limit, language })}`,
    requestInit(options, { method: 'GET' }),
  )
  const payload = await readJson(response)
  return payload?.items || []
}

export async function fetchFacetsV2(game, options = {}) {
  const response = await fetch(
    `/api/search-v2/facets${toQuery({ game: toApiGameSlug(game || '') })}`,
    requestInit(options, { method: 'GET' }),
  )
  return readJson(response)
}

export async function fetchFacetValuesV2({ game, key, q = '', limit = 30 } = {}, options = {}) {
  const response = await fetch(
    `/api/search-v2/facet-values${toQuery({ game: toApiGameSlug(game || ''), key, q, limit })}`,
    requestInit(options, { method: 'GET' }),
  )
  const payload = await readJson(response)
  return payload?.items || []
}

export async function advancedSearchV2({ game, q = '', filters = {}, sort = 'relevance', hasPrice = false, language = '', limit = 50, offset = 0 } = {}, options = {}) {
  const response = await fetch('/api/search-v2/advanced', requestInit(options, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ game: toApiGameSlug(game || ''), q, filters, sort, language, has_price: Boolean(hasPrice), limit, offset }),
  }))
  return readJson(response)
}
