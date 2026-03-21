import { GAME_CATALOG } from '../../lib/catalog/games'
import { getGameExplorerHref, getGameHref } from '../../lib/catalog/routes'

export const homeMetrics = [
  { value: '5', label: 'universos TCG activos', detail: 'Entradas reales a hubs y explorers ya operativos.' },
  { value: 'Explorer-first', label: 'navegación modular', detail: 'Arquitectura pensada para crecer sin mezclar flujos.' },
  { value: 'Ready', label: 'catálogo + pricing + marketplace', detail: 'Base visual lista para añadir stock, precios y membresías.' },
]

export const homeHeroHighlights = [
  'Catálogo por juego con jerarquía clara para escalar inventario y discovery.',
  'Base visual premium para evolucionar hacia pricing, sellers y marketplace.',
  'Navegación diseñada para pasar de home a hub, explorer y play sin fricción.',
]

export const homeMockupColumns = [
  {
    title: 'Navigation',
    items: ['Home V2', 'Catalog views', 'Game hubs', 'Explorer routes'],
  },
  {
    title: 'Signals',
    items: ['Card depth', 'Set coverage', 'Variants', 'Future pricing'],
  },
]

export const homeGames = GAME_CATALOG.map((game) => ({
  ...game,
  href:
    game.slug === 'pokemon'
      ? getGameExplorerHref(game.slug)
      : game.slug === 'onepiece'
        ? '/games/one-piece'
        : getGameHref(game.slug),
  secondaryHref: getGameHref(game.slug),
  cta: game.slug === 'pokemon' ? 'Abrir explorer' : 'Entrar al hub',
}))

export const homeBlueprintSteps = [
  {
    step: '01',
    title: 'Entrada editorial + catálogo',
    body: 'La home presenta catálogo, valor de producto y superficies preparadas para nuevas líneas de negocio.',
  },
  {
    step: '02',
    title: 'Juego como contexto principal',
    body: 'Cada universo TCG conserva su identidad y su navegación dedicada para evitar ruido entre catálogos.',
  },
  {
    step: '03',
    title: 'Explorer y detalle conectados',
    body: 'El usuario puede saltar de set a carta y de carta a print con una estructura consistente y escalable.',
  },
  {
    step: '04',
    title: 'Capas preparadas para monetización',
    body: 'Pricing, seller tools, colecciones y marketplace pueden añadirse sobre la misma base sin rehacer la home.',
  },
]

export const homeWhyItems = [
  {
    title: 'Presencia de plataforma real',
    body: 'Composición amplia, jerarquía editorial y un hero con framing de producto en lugar de una sola card centrada.',
  },
  {
    title: 'Diseño listo para crecer',
    body: 'Cada bloque funciona como una pieza reutilizable para catálogo, pricing, insights o futuras membresías.',
  },
  {
    title: 'Rutas actuales preservadas',
    body: 'Los accesos reales a Pokémon, MTG, ONE PIECE y Riftbound se mantienen sin tocar explorers, hubs ni play.',
  },
]

export const homeFinalCtaLinks = [
  { label: 'Explorar Pokémon', href: getGameExplorerHref('pokemon') },
  { label: 'Ver MTG', href: '/games/mtg' },
  { label: 'Abrir ONE PIECE', href: '/games/one-piece' },
  { label: 'Descubrir Riftbound', href: '/games/riftbound' },
]
