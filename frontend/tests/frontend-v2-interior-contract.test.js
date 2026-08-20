const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('game routes send legacy singles URLs to canonical Card results', () => {
  const page = source('app/games/[slug]/page.js')
  assert.match(page, /kind === 'card' \|\| kind === 'matches' \|\| kind === 'singles'/)
  assert.match(page, /return 'card'/)
  assert.match(page, /initialExplorerState/)
})

test('game hubs use the real catalog explorer as primary and keep advanced print search secondary', () => {
  const hub = source('components/games/GameHubPage.js')
  assert.match(hub, /import CatalogExplorer from '\.\.\/catalog\/CatalogExplorer'/)
  assert.match(hub, /<CatalogExplorer/)
  assert.match(hub, /scopedGame=\{game\.slug\}/)
  assert.match(hub, /Búsqueda avanzada de impresiones/)
  assert.match(hub, /<OnePieceSearchV2Experience game=\{game\}/)
  assert.ok(hub.indexOf('<CatalogExplorer') < hub.indexOf('<OnePieceSearchV2Experience'), 'advanced physical search must remain after the primary V2 explorer')
  assert.match(hub, /<details className="v6-advanced-search">/)
})

test('canonical Card search uses server-side Search V2 totals and page offsets instead of a browser 100-row cap', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const route = source('app/api/catalog/search/route.js')
  assert.match(explorer, /searchCatalogPage as searchCatalog/)
  assert.match(explorer, /limit: PAGE_SIZE/)
  assert.match(explorer, /offset: page \* PAGE_SIZE/)
  assert.doesNotMatch(explorer, /MAX_CANONICAL_CARDS/)
  assert.match(route, /callInternalApi\('\/api\/v2\/search'/)
  assert.match(route, /pagination_mode === 'canonical_name'/)
  assert.match(route, /total: exactTotal/)
})

test('language and Cardmarket filters remain physical-print concepts and are applied server-side', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const route = source('app/api/catalog/search/route.js')
  assert.match(explorer, /physicalFiltersActive = type === 'print' \|\| type === ''/)
  assert.match(explorer, /El idioma pertenece a la impresión física, no a la carta canónica/)
  assert.match(route, /language && item\?\.type === 'print'/)
  assert.match(route, /pricedOnly && item\?\.type !== 'print'/)
  assert.match(route, /market\?\.display_price/)
})

test('result cards distinguish canonical Cards from exact physical Prints', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /if \(item\.type === 'card'\)/)
  assert.match(card, /impresión\$\{count === 1 \? '' : 'es'\} física/)
  assert.match(card, /if \(item\?\.type !== 'print'\) return null/)
  assert.match(card, /Cardmarket \{marketPrice\}/)
})

test('Card detail explains Card to Print to Market before version selection', () => {
  const detail = source('components/cards/CardDetailLayout.js')
  assert.match(detail, /Carta canónica/)
  assert.match(detail, /1 · Carta canónica/)
  assert.match(detail, /2 · Impresión física/)
  assert.match(detail, /3 · Precio exacto/)
  assert.match(detail, /<CardVersionBrowser/)
})

test('version browser routes every selectable physical identity to an exact Print', () => {
  const browser = source('components/cards/CardVersionBrowser.js')
  assert.match(browser, /getPrintHref\(representative\.print_id\)/)
  assert.match(browser, /getPrintHref\(exactPrint\.print_id\)/)
  assert.match(browser, /getPrintHref\(print\.print_id\)/)
})

test('the complete interior V2 stylesheet is loaded globally', () => {
  const layout = source('app/layout.js')
  const css = source('app/lovable-v2-interior.css')
  assert.match(layout, /import '\.\/lovable-v2-interior\.css'/)
  assert.match(css, /\.v6-game-hero/)
  assert.match(css, /\.detail-page/)
  assert.match(css, /\.game-set-hero/)
  assert.match(css, /\.library-grid/)
})

test('no Lovable demo data is imported into the real product branch', () => {
  const hub = source('components/games/GameHubPage.js')
  const explorer = source('components/catalog/CatalogExplorer.js')
  assert.doesNotMatch(hub, /catalog\.ts|demoCards|demoPrints|synthetic/i)
  assert.doesNotMatch(explorer, /catalog\.ts|demoCards|demoPrints|synthetic/i)
})
