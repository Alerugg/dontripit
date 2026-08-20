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

test('game hubs use one CatalogExplorer and route advanced physical filters to a dedicated tool', () => {
  const hub = source('components/games/GameHubPage.js')
  const advanced = source('app/games/[slug]/advanced/page.js')
  assert.match(hub, /import CatalogExplorer from '\.\.\/catalog\/CatalogExplorer'/)
  assert.match(hub, /<CatalogExplorer/)
  assert.match(hub, /scopedGame=\{game\.slug\}/)
  assert.doesNotMatch(hub, /import OnePieceSearchV2Experience/)
  assert.doesNotMatch(hub, /<details className="v6-advanced-search">/)
  assert.match(hub, /\/games\/\$\{game\.slug\}\/advanced\?advanced=1/)
  assert.match(hub, /Abrir filtros físicos avanzados/)
  assert.match(advanced, /OnePieceSearchV2Experience/)
  assert.match(advanced, /next\.set\('advanced', '1'\)/)
  assert.match(advanced, /Volver al Explorer de/)
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

test('result cards distinguish canonical Cards, exact Prints and Sets', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /Carta canónica/)
  assert.match(card, /Impresión exacta/)
  assert.match(card, /Cobertura física/)
  assert.match(card, /Cardmarket exacto/)
  assert.match(card, /Contenido del set/)
  assert.match(card, /if \(item\?\.type !== 'print'\) return null/)
  assert.match(card, /const raw = item\?\.market\?\.display_price/)
  assert.doesNotMatch(card, /cardmarket_price/)
})

test('Card detail makes exact Print selection primary without inventing Card-level market data', () => {
  const detail = source('components/cards/CardDetailLayout.js')
  assert.match(detail, /Carta canónica/)
  assert.match(detail, /Impresiones físicas/)
  assert.match(detail, /Solo en la impresión exacta/)
  assert.match(detail, /No agregamos precios de distintas ediciones, idiomas o acabados/)
  assert.match(detail, /prints_pagination\?\.total/)
  assert.match(detail, /<CardVersionBrowser/)
  assert.doesNotMatch(detail, /cardmarket_price|display_price|price_market/)
})

test('version browser routes every selectable physical identity to an exact Print', () => {
  const browser = source('components/cards/CardVersionBrowser.js')
  assert.match(browser, /getPrintHref\(representative\.print_id\)/)
  assert.match(browser, /getPrintHref\(exactPrint\.print_id\)/)
  assert.match(browser, /getPrintHref\(print\.print_id\)/)
  assert.match(browser, /Abrir impresión exacta/)
  assert.match(browser, /Selecciona idioma \/ impresión/)
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
