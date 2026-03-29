function normalizeCode(value = '') {
  return String(value || '').trim().toLowerCase()
}

const POKEMON_SET_ORDER = [
  // Classic / WOTC
  'bs', 'ju', 'fo', 'b2', 'tr', 'g1', 'g2',

  // Neo / Legendary / e-Card
  'n1', 'n2', 'n3', 'n4', 'lc', 'ex', 'aq', 'sk',

  // ex
  'rs', 'ss', 'dr', 'ma', 'hl', 'rg', 'trr', 'dx', 'em', 'uf', 'ds', 'lm', 'hp', 'cg', 'df', 'pk',

  // Diamond & Pearl / Platinum / HGSS
  'dp', 'mt', 'sw', 'ge', 'md', 'la', 'sf',
  'pl', 'rr', 'sv', 'ar',
  'hs', 'ul', 'ud', 'tm', 'col',

  // Black & White
  'blw', 'epo', 'nvi', 'nxd', 'dex', 'drx', 'drv', 'bcr', 'pls', 'plf', 'plb', 'ltr',

  // XY
  'xy', 'flf', 'ffi', 'phf', 'prc', 'dcr', 'ros', 'aor', 'bkt', 'bkp', 'gen', 'fco', 'sts', 'evo',

  // Sun & Moon
  'sum', 'gri', 'bus', 'slg', 'cin', 'upr', 'fli', 'ces', 'drm', 'lot', 'teu', 'det', 'unb', 'unm', 'hif', 'cec',

  // Sword & Shield
  'ssh', 'rcl', 'daa', 'cpa', 'viv', 'shf', 'bst', 'cre', 'evs', 'cel', 'fst', 'brs', 'asr', 'pgo', 'lor', 'sit', 'crz',

  // Scarlet & Violet / Mega
  'svi', 'pal', 'obf', 'mew', 'par', 'paf', 'tef', 'twm', 'sfa', 'scr', 'ssp', 'pre', 'jtg', 'dri', 'blk', 'wht',
  'meg', 'pfl', 'asc', 'por', 'cri',
]

const POKEMON_ORDER_INDEX = Object.fromEntries(
  POKEMON_SET_ORDER.map((code, index) => [code, index]),
)

function compareReleaseDate(a, b) {
  const aDate = a?.release_date ? new Date(a.release_date).getTime() : Number.NaN
  const bDate = b?.release_date ? new Date(b.release_date).getTime() : Number.NaN

  const aValid = Number.isFinite(aDate)
  const bValid = Number.isFinite(bDate)

  if (aValid && bValid && aDate !== bDate) return aDate - bDate
  if (aValid && !bValid) return -1
  if (!aValid && bValid) return 1
  return 0
}

export function sortCollectionsForDisplay(gameSlug = '', collections = []) {
  const game = normalizeCode(gameSlug)

  if (!Array.isArray(collections) || collections.length === 0) return []

  const nextCollections = [...collections]

  if (game === 'pokemon') {
    nextCollections.sort((a, b) => {
      const aCode = normalizeCode(a?.code || a?.set_code)
      const bCode = normalizeCode(b?.code || b?.set_code)

      const aRank = Object.prototype.hasOwnProperty.call(POKEMON_ORDER_INDEX, aCode)
        ? POKEMON_ORDER_INDEX[aCode]
        : Number.MAX_SAFE_INTEGER

      const bRank = Object.prototype.hasOwnProperty.call(POKEMON_ORDER_INDEX, bCode)
        ? POKEMON_ORDER_INDEX[bCode]
        : Number.MAX_SAFE_INTEGER

      if (aRank !== bRank) return aRank - bRank

      const dateCompare = compareReleaseDate(a, b)
      if (dateCompare !== 0) return dateCompare

      return String(a?.name || '').localeCompare(String(b?.name || ''), undefined, {
        sensitivity: 'base',
      })
    })

    return nextCollections
  }

  nextCollections.sort((a, b) => {
    const dateCompare = compareReleaseDate(a, b)
    if (dateCompare !== 0) return dateCompare

    return String(a?.name || '').localeCompare(String(b?.name || ''), undefined, {
      sensitivity: 'base',
    })
  })

  return nextCollections
}