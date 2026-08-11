import { toApiGameSlug } from '../catalog/games'

function toQuery(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

async function readJson(response) {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.message || payload?.detail || payload?.error || 'Search V2 request failed.')
  }
  return payload
}

export async function searchV2({ q, game, limit = 24 } = {}) {
  const response = await fetch(`/api/search-v2${toQuery({ q, game: toApiGameSlug(game || ''), limit })}`, {
    method: 'GET',
    cache: 'no-store',
  })
  const payload = await readJson(response)
  return payload?.items || []
}

export async function federatedSearchV2({ q, game, page = 1, limit = 24, kind = 'all', category = '', region = '', sort = 'relevance', hasPrice = false } = {}) {
  const response = await fetch(`/api/search-v2/federated${toQuery({
    q,
    game: toApiGameSlug(game || ''),
    page,
    limit,
    kind,
    category,
    region,
    sort,
    has_price: hasPrice ? 1 : '',
  })}`, {
    method: 'GET',
    cache: 'no-store',
  })
  return readJson(response)
}

export async function suggestV2({ q, game, limit = 8 } = {}) {
  const response = await fetch(`/api/search-v2/suggest${toQuery({ q, game: toApiGameSlug(game || ''), limit })}`, {
    method: 'GET',
    cache: 'no-store',
  })
  const payload = await readJson(response)
  return payload?.items || []
}

export async function fetchFacetsV2(game) {
  const response = await fetch(`/api/search-v2/facets${toQuery({ game: toApiGameSlug(game || '') })}`, {
    method: 'GET',
    cache: 'no-store',
  })
  return readJson(response)
}

export async function fetchFacetValuesV2({ game, key, q = '', limit = 30 } = {}) {
  const response = await fetch(`/api/search-v2/facet-values${toQuery({ game: toApiGameSlug(game || ''), key, q, limit })}`, {
    method: 'GET',
    cache: 'no-store',
  })
  const payload = await readJson(response)
  return payload?.items || []
}

export async function advancedSearchV2({ game, q = '', filters = {}, sort = 'relevance', hasPrice = false, limit = 50, offset = 0 } = {}) {
  const response = await fetch('/api/search-v2/advanced', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    body: JSON.stringify({ game: toApiGameSlug(game || ''), q, filters, sort, has_price: Boolean(hasPrice), limit, offset }),
  })
  return readJson(response)
}
