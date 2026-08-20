const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('game catalog declares active and coming-soon availability explicitly', () => {
  const games = source('lib/catalog/games.js')
  for (const slug of ['pokemon', 'magic', 'onepiece', 'yugioh']) {
    const start = games.indexOf(`slug: '${slug}'`)
    const next = games.indexOf("\n  {", start + 1)
    const block = games.slice(start, next >= 0 ? next : games.length)
    assert.match(block, /availability: 'active'/, `${slug} must be active`)
  }
  const riftboundStart = games.indexOf("slug: 'riftbound'")
  const riftboundBlock = games.slice(riftboundStart, games.indexOf('\n  },', riftboundStart) + 5)
  assert.match(riftboundBlock, /availability: 'coming_soon'/)
  assert.match(games, /export const ACTIVE_GAME_CATALOG = GAME_CATALOG\.filter/)
  assert.match(games, /export function isGameCatalogActive/)
})

test('global game selector exposes only active catalogs', () => {
  const games = source('lib/catalog/games.js')
  const explorer = source('components/catalog/CatalogExplorer.js')
  assert.match(games, /\.\.\.ACTIVE_GAME_CATALOG\.map/)
  assert.doesNotMatch(games, /\.\.\.GAME_CATALOG\.map\(\(game\) => \(\{ value: game\.slug/)
  assert.match(explorer, /GAME_OPTIONS/)
})

test('game hub uses availability state instead of a Riftbound name special-case', () => {
  const page = source('app/games/[slug]/page.js')
  assert.match(page, /isGameCatalogActive\(game\.slug\)/)
  assert.match(page, /<RiftboundComingSoonPage game=\{game\} \/>/)
  assert.doesNotMatch(page, /game\.slug === 'riftbound'/)
})

test('inactive catalogs cannot expose advanced search or set routes', () => {
  const advanced = source('app/games/[slug]/advanced/page.js')
  const sets = source('app/games/[slug]/sets/page.js')
  const setDetail = source('app/games/[slug]/sets/[setCode]/page.js')
  for (const route of [advanced, sets, setDetail]) {
    assert.match(route, /isGameCatalogActive/)
    assert.match(route, /redirect\(`\/games\/\$\{game\.slug\}`\)/)
  }
  assert.match(advanced, /robots: \{ index: false, follow: true \}/)
  assert.match(sets, /robots: \{ index: false, follow: true \}/)
  assert.match(setDetail, /robots: \{ index: false, follow: true \}/)
})

test('inactive catalogs cannot leak through canonical Card or global Print deep links', () => {
  const card = source('app/games/[slug]/cards/[cardId]/layout.js')
  const print = source('app/prints/[id]/layout.js')
  assert.match(card, /if \(!isGameCatalogActive\(game\.slug\)\) redirect\(`\/games\/\$\{game\.slug\}`\)/)
  assert.match(card, /robots: \{ index: false, follow: true \}/)
  assert.match(print, /const loadPrint = cache/)
  assert.match(print, /if \(game && !isGameCatalogActive\(game\.slug\)\) redirect\(`\/games\/\$\{game\.slug\}`\)/)
  assert.match(print, /robots: \{ index: false, follow: true \}/)
})

test('coming-soon surface explicitly refuses incomplete or noncanonical catalog data', () => {
  const page = source('components/games/RiftboundComingSoonPage.js')
  assert.match(page, /no publicar datos incompletos o de fuentes no canónicas/)
  assert.match(page, /Sin datos de relleno/)
  assert.match(page, /Integración oficial pendiente/)
})

test('sitemap keeps coming-soon catalog out of indexed game hubs', () => {
  const sitemap = source('app/sitemap.js')
  assert.doesNotMatch(sitemap, /\/games\/riftbound/)
  for (const slug of ['pokemon', 'magic', 'onepiece', 'yugioh']) {
    assert.ok(sitemap.includes(`'/games/${slug}'`), `missing active game ${slug}`)
  }
})
