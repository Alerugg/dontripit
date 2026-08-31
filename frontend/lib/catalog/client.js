import { toApiGameSlug } from './games'

const RESPONSE_CACHE = new Map()
const FIVE_MINUTES = 5 * 60 * 1000
const SEARCH_TIMEOUT_MS = 15000
const FIRST_CARD_PAGE_TIMEOUT_MS = 30000
const SUGGEST_TIMEOUT_MS = 8000

function toQuery(params = {}) {
  const search = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })

  const query = search.toString()
  return query ? `?${query}` : ''
}

function optionalNumber(value, fallback = null) {
  if (value === null || value === undefined || value === '') return fallback
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function normalizeCounts(payload = {}, fallbackTotal = null) {
  return {
    card: optionalNumber(payload?.counts?.card),
    print: optionalNumber(payload?.counts?.print),
    set: optionalNumber(payload?.counts?.set),
    all: optionalNumber(payload?.counts?.all, fallbackTotal),
  }
}

async function request(path, params, { ttlMs = 0, signal, timeoutMs = 0 } = {}) {
  const url = `${path}${toQuery(params)}`
  const now = Date.now()

  if (ttlMs > 0) {
    const cached = RESPONSE_CACHE.get(url)
    if (cached && cached.expiresAt > now) return cached.promise
  }

  const timeoutController = timeoutMs > 0 ? new AbortController() : null
  let timeoutId = null
  let timedOut = false
  let removeExternalAbort = null
  let requestSignal = signal

  if (timeoutController) {
    requestSignal = timeoutController.signal
    if (signal) {
      const abortFromExternal = () => timeoutController.abort(signal.reason)
      if (signal.aborted) abortFromExternal()
      else {
        signal.addEventListener('abort', abortFromExternal, { once: true })
        removeExternalAbort = () => signal.removeEventListener('abort', abortFromExternal)
      }
    }
    timeoutId = setTimeout(() => {
      timedOut = true
      timeoutController.abort()
    }, timeoutMs)
  }

  const cleanup = () => {
    if (timeoutId) clearTimeout(timeoutId)
    if (removeExternalAbort) removeExternalAbort()
  }

  const promise = fetch(url, {
    method: 'GET',
    cache: 'no-store',
    signal: requestSignal,
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
    if (payload === null) {
      throw new Error('La respuesta del catálogo no es válida. Intenta de nuevo en unos segundos.')
    }
    return payload
  }).catch((error) => {
    if (timedOut && error?.name === 'AbortError') {
      const timeoutError = new Error('La búsqueda está tardando demasiado. Intenta de nuevo en unos segundos.')
      timeoutError.name = 'TimeoutError'
      throw timeoutError
    }
    throw error
  }).finally(cleanup)

  if (ttlMs > 0) {
    RESPONSE_CACHE.set(url, { expiresAt: now + ttlMs, promise })
    promise.catch(() => RESPONSE_CACHE.delete(url))
  }

  return promise
}

export async function searchCatalogPage(filters = {}, options = {}) {
  const firstCanonicalCardPage = filters?.type === 'card'
    && Number(filters?.include_counts) === 0
    && Number(filters?.offset || 0) === 0
  const defaultTimeoutMs = firstCanonicalCardPage ? FIRST_CARD_PAGE_TIMEOUT_MS : SEARCH_TIMEOUT_MS
  const payload = await request('/api/catalog/search', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
  }, { ...options, timeoutMs: options.timeoutMs ?? defaultTimeoutMs })

  if (Array.isArray(payload)) {
    return {
      items: payload,
      total: payload.length,
      counts: { card: 0, print: 0, set: 0, all: payload.length },
      counts_complete: true,
      limit: Number(filters.limit || payload.length || 24),
      offset: Number(filters.offset || 0),
      has_more: false,
      next_offset: null,
      truncated: false,
      integrity: null,
    }
  }

  const total = Number(payload?.total ?? 0)
  return {
    items: payload?.items || payload?.results || [],
    total,
    counts: normalizeCounts(payload, total),
    counts_complete: payload?.counts_complete !== false,
    limit: Number(payload?.limit ?? filters.limit ?? 24),
    offset: Number(payload?.offset ?? filters.offset ?? 0),
    has_more: Boolean(payload?.has_more),
    next_offset: payload?.next_offset ?? null,
    truncated: Boolean(payload?.truncated),
    integrity: payload?.integrity || null,
  }
}

export async function fetchCatalogCounts(filters = {}, options = {}) {
  const payload = await request('/api/catalog/search', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
    counts_only: 1,
    include_counts: 1,
  }, { ...options, timeoutMs: options.timeoutMs ?? SEARCH_TIMEOUT_MS })

  return {
    counts: normalizeCounts(payload),
    counts_complete: payload?.counts_complete !== false,
    truncated: Boolean(payload?.truncated),
    integrity: payload?.integrity || null,
  }
}

export async function searchCatalog(filters = {}, options = {}) {
  const payload = await searchCatalogPage(filters, options)
  return payload.items
}

export async function suggestCatalog(filters = {}, options = {}) {
  const payload = await request('/api/search-v2/suggest', {
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
  }, { ...options, timeoutMs: options.timeoutMs ?? SUGGEST_TIMEOUT_MS })

  return Array.isArray(payload) ? payload : payload?.items || []
}

export async function searchOnePieceDonPage(filters = {}, options = {}) {
  const payload = await request('/api/search-v2/don', {
    q: filters?.q || '',
    limit: filters?.limit || 24,
    offset: filters?.offset || 0,
  }, { ...options, timeoutMs: options.timeoutMs ?? SEARCH_TIMEOUT_MS })

  return {
    items: payload?.items || [],
    total: Number(payload?.total ?? 0),
    limit: Number(payload?.limit ?? filters?.limit ?? 24),
    offset: Number(payload?.offset ?? filters?.offset ?? 0),
    has_more: Boolean(payload?.has_more),
    next_offset: payload?.next_offset ?? null,
    identity_scope: payload?.identity_scope || 'source_owned',
  }
}

export async function suggestOnePieceDon(filters = {}, options = {}) {
  const payload = await request('/api/search-v2/don/suggest', {
    q: filters?.q || '',
    limit: filters?.limit || 8,
  }, { ...options, timeoutMs: options.timeoutMs ?? SUGGEST_TIMEOUT_MS })
  return payload?.items || []
}

export function fetchCardById(id) {
  return request(`/api/catalog/cards/${id}`, {}, { ttlMs: FIVE_MINUTES })
}

export function fetchCardVersions(id) {
  return request(`/api/catalog/cards/${id}/versions`, {}, { ttlMs: FIVE_MINUTES })
}

export function fetchCardPrintsPage(id, options = {}) {
  return request(`/api/catalog/cards/${id}/prints`, {
    limit: options.limit ?? 24,
    offset: options.offset ?? 0,
  }, { ttlMs: FIVE_MINUTES })
}

export function fetchPrintById(id, options = {}) {
  return request(`/api/catalog/prints/${id}`, {
    locale: options.locale ?? '',
  }, { ttlMs: FIVE_MINUTES })
}

export function fetchPrintPhysicalReleases(id) {
  return request(`/api/catalog/prints/${id}/physical-releases`, {}, { ttlMs: FIVE_MINUTES })
}

export async function fetchGamePrints(filters = {}) {
  const payload = await searchCatalogPage({
    ...filters,
    game: toApiGameSlug(filters?.game || ''),
    type: 'print',
  })

  return payload.items
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
    kind: options.kind ?? 'card',
    q: options.q ?? '',
    sort: options.sort ?? 'number_asc',
    language: options.language ?? '',
    finish: options.finish ?? '',
    rarity: options.rarity ?? '',
    priced: options.pricedOnly ? 1 : '',
    limit: options.limit ?? 24,
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
