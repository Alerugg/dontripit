export const CATALOG_CACHE_HEADERS = Object.freeze({
  'Cache-Control': 'public, s-maxage=15, must-revalidate',
})

const OPTIONAL_METADATA_PLACEHOLDERS = new Set([
  '',
  '-',
  '?',
  'n/a',
  'na',
  'none',
  'null',
  'unknown',
  'undefined',
])

export function cleanOptionalMetadata(value) {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string') return value
  const clean = value.trim()
  if (!clean || OPTIONAL_METADATA_PLACEHOLDERS.has(clean.toLowerCase())) return null
  return clean
}

export function firstCleanMetadata(...values) {
  for (const value of values) {
    const clean = cleanOptionalMetadata(value)
    if (clean !== null) return clean
  }
  return null
}
