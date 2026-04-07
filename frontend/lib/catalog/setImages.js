function normalizeGameSlug(value = '') {
  return String(value || '').trim().toLowerCase()
}

function compactSetCode(value = '') {
  return String(value || '').trim().replace(/\s+/g, '')
}

export function getLocalSetImageCandidates(game, setCode = '') {
  const gameSlug = normalizeGameSlug(game)
  const rawCode = String(setCode || '').trim()

  if (!gameSlug || !rawCode) return []

  const compact = compactSetCode(rawCode)
  const lower = compact.toLowerCase()
  const upper = compact.toUpperCase()

  return [...new Set([
    `/sets/${gameSlug}/${rawCode}.png`,
    `/sets/${gameSlug}/${compact}.png`,
    `/sets/${gameSlug}/${lower}.png`,
    `/sets/${gameSlug}/${upper}.png`,
  ])]
}

export function getPrimaryLocalSetImage(game, setCode = '') {
  return getLocalSetImageCandidates(game, setCode)[0] || ''
}