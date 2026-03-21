export const GAME_CATALOG = [
  {
    slug: 'pokemon',
    name: 'Pokémon',
    eyebrow: 'Kanto to Paldea',
    description: 'Explora cartas, variantes y sets de Pokémon con búsqueda rápida por nombre, número y expansión.',
    accent: 'var(--game-pokemon)',
  },
  {
    slug: 'mtg',
    name: 'Magic: The Gathering',
    eyebrow: 'Commander • Modern • Draft',
    description: 'Encuentra cartas y prints de MTG filtrando por colección, idioma y variantes de impresión.',
    accent: 'var(--game-mtg)',
  },
  {
    slug: 'yugioh',
    name: 'Yu-Gi-Oh!',
    eyebrow: 'TCG + OCG vibes',
    description: 'Busca monstruos, staples y ediciones especiales sin salir del universo Yu-Gi-Oh!.',
    accent: 'var(--game-yugioh)',
  },
  {
    slug: 'onepiece',
    name: 'ONE PIECE Card Game',
    eyebrow: 'Straw Hats ready',
    description: 'Navega leaders, personajes y rarezas del juego de ONE PIECE con una UI enfocada en descubrimiento.',
    accent: 'var(--game-onepiece)',
  },
  {
    slug: 'riftbound',
    name: 'Riftbound',
    eyebrow: 'League TCG',
    description: 'Descubre prints, artes y variantes del catálogo Riftbound en un explorador dedicado.',
    accent: 'var(--game-riftbound)',
  },
]

export const GAME_OPTIONS = [
  { value: '', label: 'Todos los juegos' },
  ...GAME_CATALOG.map((game) => ({ value: game.slug, label: game.name })),
]

export function getGameConfig(slug) {
  return GAME_CATALOG.find((game) => game.slug === slug) || null
}
