export const DEFAULT_LIMIT = 20
export const DEFAULT_OFFSET = 0

export function buildApiPath(action, params) {
  const query = new URLSearchParams()

  if (action === 'health') {
    return '/api/v1/health'
  }

  if (action === 'games') {
    return '/api/v1/games'
  }

  if (action === 'cardById') {
    return `/api/v1/cards/${params.cardId || '1'}`
  }

  if (action === 'printById') {
    return `/api/v1/prints/${params.printId || '1'}`
  }

  if (params.game) {
    query.set('game', params.game)
  }
  if (params.q) {
    query.set('q', params.q)
  }

  query.set('limit', String(params.limit ?? DEFAULT_LIMIT))
  query.set('offset', String(params.offset ?? DEFAULT_OFFSET))

  if (action === 'search') {
    return `/api/v1/search?${query.toString()}`
  }

  throw new Error(`Acción desconocida: ${action}`)
}

export function parseNumberInput(value, fallbackValue) {
  const parsed = Number(value)
  if (Number.isNaN(parsed) || parsed < 0) {
    return fallbackValue
  }
  return parsed
}
