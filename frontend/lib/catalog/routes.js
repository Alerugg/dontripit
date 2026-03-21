export function getGameHref(slug) {
  return `/games/${slug}`
}

export function getGameExplorerHref(slug) {
  return `/games/${slug}/explorer`
}

export function getSetHref(slug, setCode) {
  return `/games/${slug}/sets/${encodeURIComponent(setCode)}`
}

export function getCardHref(slug, cardId) {
  return slug ? `/games/${slug}/cards/${cardId}` : `/cards/${cardId}`
}

export function getPrintHref(printId) {
  return `/prints/${printId}`
}
