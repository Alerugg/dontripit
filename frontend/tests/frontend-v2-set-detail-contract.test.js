const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('set detail is canonical-card first with exact Prints as an explicit secondary tab', () => {
  const page = source('components/games/GameSetPage.js')
  assert.match(page, /const \[kind, setKind\] = useState\('card'\)/)
  assert.match(page, /Cartas canónicas/)
  assert.match(page, /Impresiones físicas/)
  assert.match(page, /role="tablist"/)
  assert.match(page, /aria-selected=\{kind === 'card'\}/)
  assert.match(page, /aria-selected=\{kind === 'print'\}/)
  assert.match(page, /<ResultsGrid items=\{items\} view=\{view\}/)
})

test('set BFF groups physical rows by real card_id instead of inventing canonical identities', () => {
  const route = source('app/api/catalog/set-detail/route.js')
  assert.match(route, /function buildCanonicalCards\(prints\)/)
  assert.match(route, /if \(!print\.card_id\) continue/)
  assert.match(route, /const key = String\(print\.card_id\)/)
  assert.match(route, /type: 'card'/)
  assert.match(route, /variant_count: variants\.length/)
})

test('set filters remain print-aware and exact-price coverage never becomes a universal card price', () => {
  const route = source('app/api/catalog/set-detail/route.js')
  const page = source('components/games/GameSetPage.js')
  assert.match(route, /function exactMarketPrice/)
  assert.match(route, /pricedOnly && exactMarketPrice\(print\) === null/)
  assert.match(route, /priced_count: prices\.length/)
  assert.match(route, /price_coverage:/)
  assert.match(page, /Solo con precio exacto/)
  assert.match(page, /Una carta canónica nunca recibe un precio universal/)
})

test('set detail exposes Lovable-style filters, views, stats and numbered pagination', () => {
  const page = source('components/games/GameSetPage.js')
  assert.match(page, /Idioma de impresión/)
  assert.match(page, /Acabado/)
  assert.match(page, /Rareza/)
  assert.match(page, /Cobertura exacta/)
  assert.match(page, /setView\('grid'\)/)
  assert.match(page, /setView\('list'\)/)
  assert.match(page, /pageWindow\(safePage, totalPages\)/)
  assert.match(page, /Página \$\{value\}/)
})
