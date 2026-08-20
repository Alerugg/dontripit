const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('Explorer requests one truthful server page instead of sorting the first 100 in the browser', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  assert.match(explorer, /searchCatalogPage as searchCatalog/)
  assert.match(explorer, /limit: PAGE_SIZE/)
  assert.match(explorer, /offset: page \* PAGE_SIZE/)
  assert.match(explorer, /setTotal\(result\.total\)/)
  assert.match(explorer, /setCounts\(result\.counts\)/)
  assert.doesNotMatch(explorer, /MAX_CANONICAL_CARDS/)
  assert.doesNotMatch(explorer, /filteredItems\.slice/)
})

test('catalog search BFF exhausts print and set pages and uses exhaustive Search V2 canonical-card totals', () => {
  const route = source('app/api/catalog/search/route.js')
  assert.match(route, /fetchAllLegacyRows/)
  assert.match(route, /offset \+= batchSize/)
  assert.match(route, /callInternalApi\('\/api\/v2\/search'/)
  assert.match(route, /pagination_mode === 'canonical_name'/)
  assert.match(route, /counts = \{/)
  assert.match(route, /card: pricedOnly \? 0 : cardCount/)
  assert.match(route, /print: filteredPrints\.length/)
  assert.match(route, /set: pricedOnly \? 0 : filteredSets\.length/)
})

test('exact-price filtering and price sorting fail closed if current Cardmarket enrichment is unavailable', () => {
  const route = source('app/api/catalog/search/route.js')
  assert.match(route, /needsGlobalMarket = pricedOnly \|\| sort === 'price_asc' \|\| sort === 'price_desc'/)
  assert.match(route, /if \(!enriched\.complete\) return responseError\(enriched\.failedUpstream\)/)
  assert.match(route, /market\?\.display_price/)
  assert.doesNotMatch(route, /cardmarket_price/)
})

test('Explorer result tabs show real counts returned by the BFF', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  assert.match(explorer, /countKey: 'card'/)
  assert.match(explorer, /countKey: 'print'/)
  assert.match(explorer, /countKey: 'set'/)
  assert.match(explorer, /countKey: 'all'/)
  assert.match(explorer, /counts\[option\.countKey\]\.toLocaleString\(\)/)
})

test('Explorer pagination is shareable and restored on global and scoped game routes', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const globalPage = source('app/explorer/page.js')
  const gamePage = source('app/games/[slug]/page.js')
  const gameHub = source('components/games/GameHubPage.js')
  assert.match(explorer, /initialPage = 1/)
  assert.match(explorer, /params\.set\('page', String\(page \+ 1\)\)/)
  assert.match(globalPage, /initialPage=\{page\}/)
  assert.match(gamePage, /page: positivePage\(query\?\.page\)/)
  assert.match(gameHub, /initialPage=\{initialExplorerState\.page \|\| 1\}/)
})
