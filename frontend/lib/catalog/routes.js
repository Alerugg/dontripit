import { normalizeGameSlug } from './games'

export function getGameHref(slug) {
  return `/games/${normalizeGameSlug(slug)}`
}

export function getGameExplorerHref(slug) {
  return getGameHref(slug)
}

export function getSetHref(slug, setCode) {
  return `${getGameHref(slug)}/sets/${encodeURIComponent(setCode)}`
}

export function getCardHref(slug, cardId, params) {
  const href = slug ? `${getGameHref(slug)}/cards/${cardId}` : `/cards/${cardId}`
  if (!params) return href
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `${href}?${query}` : href
}

export function getPrintHref(printId) {
  return `/prints/${printId}`
}
