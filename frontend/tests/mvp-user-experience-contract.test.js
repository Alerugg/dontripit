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

test('collector app routes are account-gated by the Next proxy', () => {
  const proxy = source('proxy.js')
  assert.match(proxy, /dri_session/)
  assert.match(proxy, /PUBLIC_PATHS/)
  assert.match(proxy, /\/register/)
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

test('exact One Piece collector results render as separate physical editions', () => {
  const results = source('components/searchV2/SearchV2Results.js')
  const backendRoute = source('../backend/app/routes/search_v2.py')
  const exactSearch = source('../backend/app/search_v2/onepiece_exact_collector.py')
  assert.match(results, /item\.type === 'print'/)
  assert.match(results, /Ediciones exactas/)
  assert.match(backendRoute, /exact_onepiece_collector_search/)
  assert.match(exactSearch, /canonical_card_key = f"onepiece:\{collector\}"/)
  assert.match(exactSearch, /Card\.card_key == canonical_card_key/)
  assert.doesNotMatch(exactSearch, /PARTITION BY card_id/)
})

test('price UI refuses to invent missing values', () => {
  const printPage = source('app/prints/[id]/page.js')
  const library = source('components/library/LibraryPage.js')
  assert.match(printPage, /Sin precio verificado todavía/)
  assert.match(printPage, /fuente y una fecha/)
  assert.match(library, /No mostramos estimaciones sin fuente verificada/)
})
