export function normalizeSetCode(value) {
  return String(value || '').trim().toLowerCase()
}

export function toCount(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function isNumericLike(value) {
  return /^\d+$/.test(String(value || '').trim())
}

export function pickDisplayName(...candidates) {
  const values = candidates
    .map((value) => String(value || '').trim())
    .filter(Boolean)

  const firstNonNumeric = values.find((value) => !isNumericLike(value))
  return firstNonNumeric || values[0] || ''
}

export function pickSetCode(item = {}, searchFallback = null) {
  const candidates = [
    item.code,
    item.set_code,
    searchFallback?.set_code,
    searchFallback?.code,
    searchFallback?.subtitle,
  ]
    .map((value) => String(value || '').trim())
    .filter(Boolean)

  const firstUseful = candidates.find((value) => !isNumericLike(value))
  return firstUseful || candidates[0] || ''
}

export function normalizeSet(item = {}, searchFallback = null) {
  const code = pickSetCode(item, searchFallback)
  const displayName = pickDisplayName(
    item.name,
    item.title,
    item.set_name,
    searchFallback?.title,
    searchFallback?.name,
    searchFallback?.set_name,
    code,
    `Set #${item.id || searchFallback?.id || ''}`.trim(),
  )
  const baseCount = toCount(item.card_count ?? item.count ?? item.total_cards, 0)
  const searchCount = toCount(searchFallback?.variant_count ?? searchFallback?.card_count, 0)

  return {
    id: item.id ?? searchFallback?.id,
    code,
    set_code: code,
    name: displayName,
    title: displayName,
    game: item.game_slug || searchFallback?.game || '',
    game_slug: item.game_slug || searchFallback?.game || '',
    card_count: Math.max(baseCount, searchCount),
  }
}

export function buildSearchMaps(searchItems = []) {
  const byCode = new Map()
  const byId = new Map()

  searchItems.forEach((item) => {
    const codeKey = normalizeSetCode(item?.set_code || item?.code)
    if (codeKey && !byCode.has(codeKey)) byCode.set(codeKey, item)

    const idKey = String(item?.id || '').trim()
    if (idKey && !byId.has(idKey)) byId.set(idKey, item)
  })

  return { byCode, byId }
}

export function selectBestSearchFallback(item = {}, results = []) {
  if (!Array.isArray(results) || results.length === 0) return null

  const itemId = String(item?.id || '').trim()
  const itemCode = normalizeSetCode(item?.code || item?.set_code)
  const itemName = String(item?.name || item?.title || item?.set_name || '').trim()

  const exactMatch = results.find((result) => {
    const resultId = String(result?.id || '').trim()
    const resultCode = normalizeSetCode(result?.set_code || result?.code)
    return (itemId && resultId === itemId) || (itemCode && resultCode === itemCode)
  })
  if (exactMatch) return exactMatch

  const itemLooksDegraded = isNumericLike(itemCode) || isNumericLike(itemName)
  if (!itemLooksDegraded) return null

  const qualified = results
    .map((result) => {
      const canonicalCode = pickSetCode(result)
      const canonicalName = pickDisplayName(result?.title, result?.name, result?.set_name, canonicalCode)
      return {
        result,
        canonicalCode,
        canonicalName,
      }
    })
    .filter(({ canonicalCode, canonicalName }) => canonicalCode && !isNumericLike(canonicalCode) && !isNumericLike(canonicalName))

  if (qualified.length !== 1) return null
  return qualified[0].result
}
