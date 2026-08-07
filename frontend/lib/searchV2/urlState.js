export const TRUE_FILTER_TOKEN = '__true__'

export function hasFilterValues(filters = {}) {
  return Object.values(filters).some((value) => {
    if (value === undefined || value === null || value === '') return false
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'object') {
      return Object.values(value).some((nested) => nested !== undefined && nested !== null && nested !== '')
    }
    return true
  })
}

export function readAdvancedFilters(searchParams) {
  const filters = {}
  const ranges = {}

  for (const [param, rawValue] of searchParams.entries()) {
    if (!param.startsWith('f_')) continue
    const key = param.slice(2)
    if (!key) continue

    if (key.endsWith('_min') || key.endsWith('_max')) {
      const suffix = key.endsWith('_min') ? 'min' : 'max'
      const base = key.slice(0, -4)
      const parsed = Number(rawValue)
      if (!base || !Number.isFinite(parsed)) continue
      ranges[base] = { ...(ranges[base] || {}), [suffix]: parsed }
      continue
    }

    const value = rawValue === TRUE_FILTER_TOKEN ? true : rawValue
    if (!(key in filters)) {
      filters[key] = value
    } else if (Array.isArray(filters[key])) {
      filters[key].push(value)
    } else {
      filters[key] = [filters[key], value]
    }
  }

  return { ...filters, ...ranges }
}

export function appendAdvancedFilters(params, filters = {}) {
  Object.entries(filters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return

    if (Array.isArray(value)) {
      value
        .filter((item) => item !== undefined && item !== null && item !== '')
        .forEach((item) => params.append(`f_${key}`, String(item)))
      return
    }

    if (typeof value === 'object') {
      if (value.min !== undefined && value.min !== null && value.min !== '') {
        params.set(`f_${key}_min`, String(value.min))
      }
      if (value.max !== undefined && value.max !== null && value.max !== '') {
        params.set(`f_${key}_max`, String(value.max))
      }
      return
    }

    params.set(`f_${key}`, value === true ? TRUE_FILTER_TOKEN : String(value))
  })
}

export function safePage(rawValue) {
  const parsed = Number(rawValue || 1)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}
