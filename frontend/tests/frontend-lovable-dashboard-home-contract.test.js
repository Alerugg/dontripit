const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('Dashboard remains an account workspace backed by real account/library APIs', () => {
  const dashboard = source('components/dashboard/DashboardPage.js')
  const css = source('app/lovable-v2-home-dashboard.css')

  assert.match(dashboard, /fetch\('\/api\/auth\/me'/)
  assert.match(dashboard, /fetch\('\/api\/library\/collection'/)
  assert.match(dashboard, /fetch\('\/api\/library\/wishlist'/)
  assert.match(dashboard, /Valor conservador\*/)
  assert.match(dashboard, /El resto no se estima/)
  assert.match(css, /\.v13-dashboard[\s\S]*width:\s*min\(1220px/)
  assert.match(css, /\.v13-search-workspace[\s\S]*background:\s*#0d0d10/)
})

test('Home preserves the approved hero while secondary surfaces use V2 parity', () => {
  const home = source('components/home/PublicHome.js')
  const layout = source('app/layout.js')
  const css = source('app/lovable-v2-home-dashboard.css')

  assert.match(home, /<section className="v5-hero"/)
  assert.match(home, /TCG Data\.<br \/>/)
  assert.match(home, /<em>Pricing\.<\/em>/)
  assert.match(home, /Liquidity\./)
  assert.match(layout, /import '\.\/lovable-v2-home-dashboard\.css'/)
  assert.doesNotMatch(css, /\.v5-hero\s*\{/)
})

test('Home avoids unsupported cadence and fabricated valuation promises', () => {
  const home = source('components/home/PublicHome.js')
  assert.doesNotMatch(home, />24h</)
  assert.doesNotMatch(home, /se actualiza a diario/)
  assert.match(home, /fuente y procedencia visibles/)
  assert.match(home, /Sin fechas inventadas/)
  assert.match(home, /Las versiones sin precio seguro no se estiman/)
})

test('Dashboard/Home parity includes mobile and reduced-motion handling', () => {
  const css = source('app/lovable-v2-home-dashboard.css')
  assert.match(css, /@media \(max-width:\s*760px\)/)
  assert.match(css, /@media \(prefers-reduced-motion:\s*reduce\)/)
})
