export const GAME_CATALOG = [
  {
    slug: 'pokemon',
    name: 'Pokémon',
    eyebrow: 'Kanto → Paldea',
    description: 'Explora cartas, variantes, sets y producto sellado de Pokémon con una navegación clara.',
    accent: 'var(--game-pokemon)',
  },
  {
    slug: 'magic',
    name: 'Magic: The Gathering',
    eyebrow: 'Standard • Commander • Modern',
    description: 'Busca cartas de Magic y abre sus variantes, sets y producto sellado desde una sola ruta.',
    accent: 'var(--game-magic)',
  },
  {
    slug: 'onepiece',
    name: 'ONE PIECE Card Game',
    eyebrow: 'Leaders • Characters • Events',
    description: 'Navega el catálogo de One Piece con resultados limpios por carta y variantes dentro de cada ficha.',
    accent: 'var(--game-onepiece)',
  },
  {
    slug: 'yugioh',
    name: 'Yu-Gi-Oh!',
    eyebrow: 'TCG competitivo',
    description: 'Encuentra staples, arquetipos y ediciones de Yu-Gi-Oh! con una UX enfocada en claridad y velocidad.',
    accent: 'var(--game-yugioh)',
  },
  {
    slug: 'riftbound',
    name: 'Riftbound',
    eyebrow: 'League TCG',
    description: 'Explora el catálogo de Riftbound con vistas listas para colecciones, torneos y noticias.',
    accent: 'var(--game-riftbound)',
  },
]

export const GAME_OPTIONS = [
  { value: '', label: 'Todos los juegos' },
  ...GAME_CATALOG.map((game) => ({ value: game.slug, label: game.name })),
]

const GAME_SLUG_ALIASES = {
  mtg: 'magic',
  'one-piece': 'onepiece',
}

export function normalizeGameSlug(slug = '') {
  return GAME_SLUG_ALIASES[slug] || slug
}

export function getGameConfig(slug) {
  const normalizedSlug = normalizeGameSlug(slug)
  return GAME_CATALOG.find((game) => game.slug === normalizedSlug) || null
}
