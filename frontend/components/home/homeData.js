import { GAME_CATALOG } from '../../lib/catalog/games'
import { getGameExplorerHref, getGameHref } from '../../lib/catalog/routes'

function getHubHref(slug) {
  return slug === 'onepiece' ? '/games/one-piece' : getGameHref(slug)
}

function getExplorerHref(slug) {
  return slug === 'onepiece' ? '/games/one-piece' : getGameExplorerHref(slug)
}

export const homeHeroStats = [
  'Entrada por TCG',
  'Sets + variantes',
  'Wishlist + colección',
]

export const homeHeroLayers = [
  {
    eyebrow: 'Catalog signal',
    title: 'Autocompletado con miniaturas',
    body: 'Búsquedas rápidas para saltar de nombre a carta, set o print sin fricción.',
  },
  {
    eyebrow: 'Structure',
    title: 'Variantes claras',
    body: 'Prints, rarezas y versiones enlazadas con una jerarquía que no se pierde.',
  },
  {
    eyebrow: 'Roadmap',
    title: 'Marketplace-ready',
    body: 'La base visual queda preparada para pricing, stock, sellers y colección.',
  },
]

export const homeGames = [
  {
    slug: 'pokemon',
    label: 'Live catalog',
    glow: 'var(--home-gold-glow)',
    cta: 'Explorar Pokémon',
    href: getGameExplorerHref('pokemon'),
    secondaryHref: getHubHref('pokemon'),
  },
  {
    slug: 'mtg',
    label: 'Deep sets',
    glow: 'var(--home-violet-glow)',
    cta: 'Abrir hub MTG',
    href: getHubHref('mtg'),
    secondaryHref: getExplorerHref('mtg'),
  },
  {
    slug: 'yugioh',
    label: 'Print clarity',
    glow: 'var(--home-blue-glow)',
    cta: 'Ver Yu-Gi-Oh!',
    href: getHubHref('yugioh'),
    secondaryHref: getExplorerHref('yugioh'),
  },
  {
    slug: 'onepiece',
    label: 'Collector flow',
    glow: 'var(--home-cyan-glow)',
    cta: 'Entrar a One Piece',
    href: getHubHref('onepiece'),
    secondaryHref: getExplorerHref('onepiece'),
  },
  {
    slug: 'riftbound',
    label: 'Next dataset',
    glow: 'var(--home-blue-glow)',
    cta: 'Descubrir Riftbound',
    href: getHubHref('riftbound'),
    secondaryHref: getExplorerHref('riftbound'),
  },
].map((entry) => {
  const game = GAME_CATALOG.find((item) => item.slug === entry.slug)
  return {
    ...game,
    ...entry,
    hubHref: getHubHref(entry.slug),
    explorerHref: getExplorerHref(entry.slug),
  }
})

export const homeBlueprintSteps = [
  {
    step: '01',
    title: 'TCG',
    body: 'Cada universo arranca con su propio hub para mantener discovery, contexto y navegación limpia.',
  },
  {
    step: '02',
    title: 'Colección / Set',
    body: 'Las expansiones organizan bloques navegables y permiten leer el catálogo por lanzamiento o serie.',
  },
  {
    step: '03',
    title: 'Carta',
    body: 'La ficha centraliza datos clave, relaciones y acceso a prints sin duplicar información.',
  },
  {
    step: '04',
    title: 'Print / Variante',
    body: 'La capa final deja claras rarezas, idiomas, acabados y espacio futuro para pricing o stock.',
  },
]

export const homeFeatureList = [
  'Búsquedas rápidas con autocompletado y miniaturas listas para discovery serio.',
  'Hubs por juego para no mezclar contextos ni romper la lectura del catálogo.',
  'Variantes enlazadas con estructura set > carta > print fácil de seguir.',
  'Datos clave claros para explorar, comparar y preparar pricing futuro.',
  'Base modular para wishlist, colección, sellers y marketplace sin rehacer UX.',
  'Experiencia usable en desktop y mobile con paneles consistentes y CTAs reales.',
]

export const homeFeaturePanels = [
  {
    label: 'Search layer',
    value: 'Autocomplete',
    note: 'Miniaturas + coincidencias rápidas',
  },
  {
    label: 'Catalog model',
    value: 'Set → Card → Print',
    note: 'Jerarquía legible para datasets grandes',
  },
  {
    label: 'Collector tools',
    value: 'Wishlist / Collection',
    note: 'Superficies preparadas para el flujo completo',
  },
]

export const homeFaqItems = [
  {
    question: '¿Qué TCGs están disponibles ahora?',
    answer: 'La home enlaza directamente a Pokémon, Magic: The Gathering, Yu-Gi-Oh!, ONE PIECE Card Game y Riftbound mediante hubs y explorers ya activos.',
  },
  {
    question: '¿La plataforma muestra variantes y prints?',
    answer: 'Sí. La arquitectura está pensada para navegar desde el juego hasta la carta y su print o variante, dejando clara la relación entre set, card y versión.',
  },
  {
    question: '¿Se puede usar como base para marketplace?',
    answer: 'Sí. El diseño y la estructura de datos dejan espacio para pricing, stock, sellers y superficies de compra sin tener que rehacer la home.',
  },
  {
    question: '¿La colección y wishlist ya forman parte del flujo?',
    answer: 'Forman parte del lenguaje de producto y de la dirección de la plataforma. La home ya las presenta como capas naturales del sistema para integrarlas de forma ordenada.',
  },
  {
    question: '¿Se ampliarán más juegos y datasets?',
    answer: 'Sí. La composición modular permite añadir nuevos TCGs, más hubs y datasets más profundos manteniendo la misma jerarquía visual y funcional.',
  },
]

export const homeFinalCtaLinks = [
  { label: 'Explorar Pokémon', href: getGameExplorerHref('pokemon') },
  { label: 'Ver hubs por juego', href: '/games/pokemon' },
]
