const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('exact print pricing delegates to the exact Cardmarket print reference', () => {
  const route = source('app/api/prices/print/[id]/route.js')
  const backend = source('../backend/app/routes/market_reference.py')
  assert.match(route, /\/api\/v1\/market\/prints\/\$\{id\}\/cardmarket/)
  assert.match(route, /const price = payload\.price \|\| null/)
  assert.match(route, /const reference = payload\.reference \|\| null/)
  assert.match(route, /price: null/)
  assert.match(route, /cardmarket: reference/)
  assert.match(route, /cardmarket_low_ex_plus_or_foil_low/)
  assert.match(backend, /currency = 'EUR'/)
  assert.match(backend, /price_low/)
  assert.match(backend, /price_mid/)
  assert.match(backend, /price_market/)
  assert.match(backend, /price_last/)
  assert.match(backend, /"source": "cardmarket"/)
})

test('print detail presents four price concepts without collapsing them', () => {
  const page = source('app/prints/[id]/page.js')
  for (const label of ['Mínimo', 'Conservador', 'Tendencia', 'Media']) {
    assert.ok(page.includes(label), `missing price label ${label}`)
  }
  assert.match(page, /Low Price EX\+/)
  assert.match(page, /Foil Low/)
  assert.match(page, /Sin Price Guide actual/)
  assert.match(page, /No reutilizamos el precio de otra edición/)
})

test('collection labels total as conservative and exposes valuation coverage', () => {
  const library = source('components/library/LibraryPage.js')
  const dashboard = source('components/dashboard/DashboardPage.js')
  assert.match(library, /Valor conservador\*/)
  assert.match(library, /valuation_coverage_count/)
  assert.match(library, /Las cartas sin esa métrica no se estiman ni se suman/)
  assert.match(dashboard, /Valor conservador\*/)
  assert.match(dashboard, /valuation_coverage_count/)
})
