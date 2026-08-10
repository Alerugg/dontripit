const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('auth UI is connected to real session endpoints', () => {
  const auth = source('components/auth/AuthShell.js')
  assert.match(auth, /\/api\/auth\/register/)
  assert.match(auth, /\/api\/auth\/login/)
  assert.doesNotMatch(auth, /Vista previa del flujo/)
  assert.match(auth, /marketing_consent/)
})

test('catalog discovery is public while personal collector routes stay account-gated', () => {
  const proxy = source('proxy.js')
  const publicRouteConfig = proxy.match(/const PUBLIC_PATHS[\s\S]+?function isPublicPath/)?.[0] || ''
  assert.match(proxy, /dri_session/)
  assert.match(proxy, /PUBLIC_PATHS/)
  assert.match(proxy, /PUBLIC_PREFIXES/)
  assert.match(publicRouteConfig, /['"]\/games\/['"]/)
  assert.match(publicRouteConfig, /['"]\/prints\/['"]/)
  assert.doesNotMatch(publicRouteConfig, /['"]\/collection['"]/)
  assert.doesNotMatch(publicRouteConfig, /['"]\/wishlist['"]/)
  assert.doesNotMatch(publicRouteConfig, /['"]\/dashboard['"]/)
  assert.match(proxy, /\/register/)
})

test('production CSP permits source-owned art for every active card catalog', () => {
  const config = source('next.config.js')
  const csp = config.match(/Content-Security-Policy[\s\S]+?upgrade-insecure-requests/)?.[0] || ''
  for (const hostname of [
    'cards.scryfall.io',
    'en.onepiece-cardgame.com',
    'assets.tcgdex.net',
    'images.ygoprodeck.com',
    'images.riftbound.cards',
  ]) {
    const hostnamePattern = new RegExp(hostname.replaceAll('.', '\\.'))
    assert.match(config, hostnamePattern)
    assert.match(csp, hostnamePattern)
  }
})

test('collection and wishlist use exact physical print IDs', () => {
  const actions = source('components/library/LibraryActions.js')
  const library = source('components/library/LibraryPage.js')
  assert.match(actions, /print_id: Number\(printId\)/)
  assert.match(actions, /\/api\/library\/\$\{kind\}/)
  assert.match(actions, /add\('collection'\)/)
  assert.match(actions, /add\('wishlist'\)/)
  assert.match(library, /item\.print\.id/)
})

test('exact collector results preserve physical identity and open the exact print', () => {
  const results = source('components/searchV2/SearchV2Results.js')
  const backendRoute = source('../backend/app/routes/search_v2.py')
  const exactSearch = source('../backend/app/search_v2/onepiece_exact_collector.py')
  assert.match(results, /item\.type === 'print'/)
  assert.match(results, /Versiones que coinciden/)
  assert.match(results, /`\/prints\/\$\{printId\}`/)
  assert.match(backendRoute, /exact_onepiece_collector_search/)
  assert.match(exactSearch, /canonical_card_key = f"onepiece:\{collector\}"/)
  assert.match(exactSearch, /Card\.card_key == canonical_card_key/)
  assert.doesNotMatch(exactSearch, /PARTITION BY card_id/)
})

test('search suggestions start from the first character', () => {
  const input = source('components/search/SearchInput.js')
  const experience = source('components/searchV2/OnePieceSearchV2Experience.js')
  assert.match(input, /trim\(\)\.length >= 1/)
  assert.match(experience, /query\.trim\(\)\.length < 1/)
})

test('price UI refuses to invent missing values', () => {
  const printPage = source('app/prints/[id]/page.js')
  const library = source('components/library/LibraryPage.js')
  assert.match(printPage, /Sin precio Cardmarket verificado/)
  assert.match(printPage, /No mostramos una estimación/)
  assert.match(printPage, /fuente y fecha/)
  assert.match(library, /Sin precio verificado/)
  assert.match(library, /No suma al valor conservador/)
  assert.match(library, /no se estiman ni se suman/)
})
