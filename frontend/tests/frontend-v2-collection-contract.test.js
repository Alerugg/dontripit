const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const REPO_ROOT = path.resolve(ROOT, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}
function repoSource(relativePath) {
  return fs.readFileSync(path.join(REPO_ROOT, relativePath), 'utf8')
}

test('P0.6 collection summary labels value as conservative and exposes exact valuation coverage', () => {
  const page = source('components/library/LibraryPage.js')
  assert.match(page, /Valor conservador/)
  assert.match(page, /valuation_coverage_count/)
  assert.match(page, /coveragePercent/)
  assert.match(page, /Cobertura exacta de valoración/)
  assert.match(page, /Las versiones sin valor conservador no se estiman ni se suman/)
})

test('P0.6 collection cards stay anchored to exact Print identity', () => {
  const page = source('components/library/LibraryPage.js')
  assert.match(page, /Print \{print\.id\}/)
  assert.match(page, /href=\{`\/prints\/\$\{print\.id\}`\}/)
  assert.match(page, /print\.language\?\.toUpperCase/)
  assert.match(page, /print\.is_foil \? 'Foil'/)
  assert.match(page, /print_id: item\.print\.id/)
})

test('P0.6 portfolio value uses conservative exact-Print valuation and quantity', () => {
  const page = source('components/library/LibraryPage.js')
  const backend = repoSource('backend/app/routes/user_library.py')
  assert.match(page, /valuation_value \?\? item\?\.latest_price\?\.conservative/)
  assert.match(page, /conservativeValue \* quantity/)
  assert.match(page, /Posición · \{quantity\} × \{conservative\}/)
  assert.match(backend, /_current_cardmarket_price/)
  assert.match(backend, /valuation_value/)
  assert.match(backend, /known_value_eur/)
  assert.match(backend, /item\["latest_price"\]\["valuation_value"\] \* item\["quantity"\]/)
})

test('P0.6 collection supports local discovery and safe removal without changing exact-Print API writes', () => {
  const page = source('components/library/LibraryPage.js')
  assert.match(page, /Carta, set, número, idioma o Print ID/)
  assert.match(page, /Valor conservador ↓/)
  assert.match(page, /window\.confirm/)
  assert.match(page, /method: 'DELETE'/)
  assert.match(page, /method: 'POST'/)
})

test('P0.6 collection styling is responsive and reduced-motion safe', () => {
  const css = source('components/library/LibraryPage.css')
  assert.match(css, /\.v10-coverage-panel/)
  assert.match(css, /\.v10-library-toolbar/)
  assert.match(css, /\.v10-position-value/)
  assert.match(css, /@media \(max-width: 760px\)/)
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/)
})
