const VERIFIED_AT = '2026-08-09'

// V1 is intentionally curated from official product/news pages. We prefer a
// smaller verified calendar over inferring dates from catalogue metadata.
const RELEASES = [
  {
    id: 'pokemon-30th-celebration-us',
    game: 'pokemon',
    title: 'Pokémon TCG: 30th Celebration',
    release_date: '2026-09-16',
    region: 'US',
    source: 'Pokémon.com',
    source_url: 'https://www.pokemon.com/us/news/pokemon-tcg-30th-celebration-product-showcase',
    verified_at: VERIFIED_AT,
    kind: 'expansion',
  },
  {
    id: 'pokemon-30th-binder-us',
    game: 'pokemon',
    title: '30th Celebration Binder Collection',
    release_date: '2026-10-02',
    region: 'US',
    source: 'Pokémon.com',
    source_url: 'https://www.pokemon.com/us/pokemon-tcg/product-gallery/30th-celebration-binder-collection',
    verified_at: VERIFIED_AT,
    kind: 'collection',
  },
  {
    id: 'mtg-the-hobbit-global',
    game: 'magic',
    title: 'Magic: The Gathering | The Hobbit',
    release_date: '2026-08-14',
    region: 'GLOBAL',
    source: 'Magic: The Gathering',
    source_url: 'https://magic.wizards.com/en/products/the-hobbit',
    verified_at: VERIFIED_AT,
    kind: 'set',
  },
  {
    id: 'mtg-reality-fracture-global',
    game: 'magic',
    title: 'Reality Fracture',
    release_date: '2026-10-02',
    region: 'GLOBAL',
    source: 'Magic: The Gathering',
    source_url: 'https://magic.wizards.com/en/products/reality-fracture/card-image-gallery',
    verified_at: VERIFIED_AT,
    kind: 'set',
  },
  {
    id: 'mtg-star-trek-global',
    game: 'magic',
    title: 'Magic: The Gathering | Star Trek',
    release_date: '2026-11-13',
    region: 'GLOBAL',
    source: 'Magic: The Gathering',
    source_url: 'https://magic.wizards.com/en/products/star-trek/card-image-gallery',
    verified_at: VERIFIED_AT,
    kind: 'set',
  },
  {
    id: 'yugioh-magnificent-monsters-eu',
    game: 'yugioh',
    title: 'Magnificent Monsters',
    release_date: '2026-09-03',
    region: 'EU',
    source: 'Yu-Gi-Oh! TCG Europe',
    source_url: 'https://www.yugioh-card.com/eu/product/magnificent-monsters/',
    verified_at: VERIFIED_AT,
    kind: 'set',
  },
  {
    id: 'yugioh-magnificent-monsters-us',
    game: 'yugioh',
    title: 'Magnificent Monsters',
    release_date: '2026-09-04',
    region: 'US',
    source: 'Yu-Gi-Oh! TCG',
    source_url: 'https://www.yugioh-card.com/en/products/mamo/',
    verified_at: VERIFIED_AT,
    kind: 'set',
  },
]

export const REGION_LABELS = {
  GLOBAL: 'Global',
  US: 'USA',
  EU: 'Europa',
  JP: 'Japón',
  EN: 'Internacional',
}

export function getVerifiedReleases({ game = '', region = '', upcoming = true, limit = 12 } = {}) {
  const normalizedGame = String(game || '').toLowerCase() === 'mtg' ? 'magic' : String(game || '').toLowerCase()
  const normalizedRegion = String(region || '').toUpperCase()
  const today = new Date().toISOString().slice(0, 10)

  return RELEASES
    .filter((item) => !normalizedGame || item.game === normalizedGame)
    .filter((item) => !normalizedRegion || item.region === normalizedRegion || item.region === 'GLOBAL')
    .filter((item) => !upcoming || item.release_date >= today)
    .sort((a, b) => a.release_date.localeCompare(b.release_date) || a.title.localeCompare(b.title))
    .slice(0, Math.max(1, Number(limit) || 12))
}

export function getReleaseRegions(game = '') {
  return [...new Set(getVerifiedReleases({ game, upcoming: false, limit: 100 }).map((item) => item.region))]
}
