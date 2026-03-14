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
    throw new Error(payload?.message || 'No pudimos cargar datos del catálogo.')
  }

  return payload
}

export async function searchCatalog(filters) {
  const payload = await request('/api/catalog/search', filters)
  return payload.items || []
}

export async function suggestCatalog(filters) {
  const payload = await request('/api/catalog/suggest', filters)
  return payload.items || []
}

export function fetchCardById(id) {
  return request(`/api/catalog/cards/${id}`)
}

export function fetchPrintById(id) {
  return request(`/api/catalog/prints/${id}`)
}

export const GAME_OPTIONS = [
  { value: '', label: 'Todos los juegos' },
  { value: 'riftbound', label: 'Riftbound' },
  { value: 'yugioh', label: 'Yu-Gi-Oh!' },
  { value: 'pokemon', label: 'Pokémon' },
  { value: 'mtg', label: 'Magic: The Gathering' },
]

export const RESULT_TYPE_OPTIONS = [
  { value: '', label: 'Todo' },
  { value: 'card', label: 'Cartas' },
  { value: 'print', label: 'Prints' },
  { value: 'set', label: 'Sets' },
]
