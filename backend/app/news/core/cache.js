const CACHE_TTL_MS = 10 * 60 * 1000
const store = new Map()

export function getCachedNews(game) {
  const hit = store.get(game)
  if (!hit) {
    return null
  }

  if (Date.now() > hit.expiresAt) {
    store.delete(game)
    return null
  }

  return hit.value
}

export function setCachedNews(game, value) {
  store.set(game, {
    value,
    expiresAt: Date.now() + CACHE_TTL_MS,
  })
}

export function clearNewsCache() {
  store.clear()
}
