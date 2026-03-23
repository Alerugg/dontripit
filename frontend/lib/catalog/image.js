export const ONEPIECE_PROXY_PATH = '/api/catalog/image'

function encodeSrc(src) {
  try {
    return encodeURI(src)
  } catch {
    return src
  }
}

function normalizeTcgdexSetCode(segment) {
  if (!segment || !segment.startsWith('sv')) return segment
  return segment.replace(/^sv(\d)(?!\d)/i, 'sv0$1')
}

function normalizeTcgdexCollector(segment) {
  if (!/^\d+$/.test(segment || '')) return segment
  return segment.padStart(3, '0')
}

export function repairTcgdexAssetUrl(src) {
  if (typeof src !== 'string') return ''

  const trimmed = src.trim()
  if (!trimmed) return ''

  let parsed
  try {
    parsed = new URL(trimmed)
  } catch {
    return trimmed
  }

  if (parsed.hostname !== 'assets.tcgdex.net') return trimmed

  const segments = parsed.pathname.split('/').filter(Boolean)
  if (segments.length < 5) return trimmed

  const [language, block, setCode, collectorNumber, ...rest] = segments
  if (block !== 'sv') return trimmed

  const nextPath = [
    '',
    language,
    block,
    normalizeTcgdexSetCode(setCode),
    normalizeTcgdexCollector(collectorNumber),
    ...rest,
  ].join('/')

  parsed.pathname = nextPath
  return parsed.toString()
}

export function buildImageProxyUrl(src) {
  return `${ONEPIECE_PROXY_PATH}?src=${encodeURIComponent(src)}`
}

export function normalizeCatalogImageSrc(src) {
  if (typeof src !== 'string') return ''

  const trimmed = src.trim()
  if (!trimmed) return ''

  const withProtocol = trimmed.startsWith('//') ? `https:${trimmed}` : trimmed
  const repaired = repairTcgdexAssetUrl(withProtocol)

  try {
    const parsed = new URL(repaired)
    if (parsed.hostname === 'en.onepiece-cardgame.com') {
      return buildImageProxyUrl(parsed.toString())
    }
  } catch {
    return encodeSrc(repaired)
  }

  return encodeSrc(repaired)
}

export function buildCatalogImageDebugInfo({ src, normalizedSrc, status, reason }) {
  return {
    originalSrc: typeof src === 'string' ? src.trim() : '',
    normalizedSrc: normalizedSrc || '',
    status: status || 'idle',
    reason: reason || '',
  }
}
