export const GAME_CATALOG = [
  {
    slug: 'pokemon',
    name: 'Pokémon',
    eyebrow: 'Kanto → Paldea',
    description: 'Explora cartas maestras, variantes, sets y producto sellado de Pokémon sin duplicados en la búsqueda.',
    accent: 'var(--game-pokemon)',
    availability: 'active',
  },
  {
    slug: 'magic',
    name: 'Magic: The Gathering',
    eyebrow: 'Standard • Commander • Modern',
    description: 'Busca cartas maestras de Magic y abre sus variantes, sets y producto sellado desde una sola ruta.',
    accent: 'var(--game-magic)',
    availability: 'active',
  },
  {
    slug: 'onepiece',
    name: 'ONE PIECE Card Game',
    eyebrow: 'Leaders • Characters • Events',
    description: 'Navega el catálogo de One Piece con resultados limpios por carta y variantes dentro de cada ficha.',
    accent: 'var(--game-onepiece)',
    availability: 'active',
  },
  {
    slug: 'yugioh',
    name: 'Yu-Gi-Oh!',
    eyebrow: 'TCG competitivo',
    description: 'Encuentra staples, arquetipos y ediciones de Yu-Gi-Oh! con una UX enfocada en claridad y velocidad.',
    accent: 'var(--game-yugioh)',
    availability: 'active',
  },
  {
    slug: 'riftbound',
    name: 'Riftbound',
    eyebrow: 'League TCG',
    description: 'Integración oficial de Riftbound en preparación. El catálogo se activará cuando pueda publicarse con identidad física y fuentes canónicas completas.',
    accent: 'var(--game-riftbound)',
    availability: 'coming_soon',
  },
]

export const ACTIVE_GAME_CATALOG = GAME_CATALOG.filter((game) => game.availability === 'active')
export const COMING_SOON_GAME_CATALOG = GAME_CATALOG.filter((game) => game.availability === 'coming_soon')

export const GAME_OPTIONS = [
  { value: '', label: 'Todos los juegos' },
  ...ACTIVE_GAME_CATALOG.map((game) => ({ value: game.slug, label: game.name })),
]

const GAME_SLUG_ALIASES = {
  mtg: 'magic',
  'one-piece': 'onepiece',
}

const API_GAME_SLUG_ALIASES = {
  magic: 'mtg',
}

export function normalizeGameSlug(slug = '') {
  return GAME_SLUG_ALIASES[slug] || slug
}

export function toApiGameSlug(slug = '') {
  return API_GAME_SLUG_ALIASES[slug] || slug
}

export function getGameConfig(slug) {
  const normalizedSlug = normalizeGameSlug(slug)
  return GAME_CATALOG.find((game) => game.slug === normalizedSlug) || null
}

export function isGameCatalogActive(slug) {
  return getGameConfig(slug)?.availability === 'active'
}
