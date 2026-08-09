const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('exact print pricing requests raw Cardmarket EUR metrics', () => {
  const route = source('app/api/prices/print/[id]/route.js')
  assert.match(route, /source:\s*'cardmarket'/)
  assert.match(route, /currency:\s*'EUR'/)
  assert.match(route, /granularity:\s*'raw'/)
  assert.match(route, /conservative = latest\.mid/)
  assert.match(route, /minimum = latest\.low/)
  assert.match(route, /trend = latest\.market/)
  assert.match(route, /average = latest\.last/)
})

test('print detail presents four price concepts without collapsing them', () => {
  const page = source('app/prints/[id]/page.js')
  for (const label of ['Mínimo', 'Conservador', 'Tendencia', 'Media']) {
    assert.ok(page.includes(label), `missing price label ${label}`)
  }
  assert.match(page, /Low Price EX\+/)
  assert.match(page, /Foil Low/)
  assert.match(page, /Sin precio Cardmarket verificado/)
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
