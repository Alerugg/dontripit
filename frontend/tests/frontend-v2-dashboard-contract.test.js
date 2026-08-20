const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('dashboard is anchored to real account collection and wishlist data', () => {
  const page = source('components/dashboard/DashboardPage.js')
  assert.match(page, /fetch\('\/api\/auth\/me'/)
  assert.match(page, /fetch\('\/api\/library\/collection'/)
  assert.match(page, /fetch\('\/api\/library\/wishlist'/)
  assert.match(page, /collection\.known_value_eur/)
  assert.match(page, /collection\.valuation_coverage_count/)
  assert.match(page, /Valor conservador\*/)
  assert.match(page, /El resto no se estima/)
})

test('dashboard search enters canonical Card results instead of guessing an exact Print', () => {
  const page = source('components/dashboard/DashboardPage.js')
  assert.match(page, /params\.set\('kind', 'card'\)/)
  assert.match(page, /params\.set\('view', 'grid'\)/)
  assert.match(page, /router\.push\(`\/games\/\$\{selectedGame\}\?\$\{params\.toString\(\)\}#buscar`\)/)
})

test('recent collection activity remains exact-Print navigation', () => {
  const page = source('components/dashboard/DashboardPage.js')
  assert.match(page, /function RecentPrintCard/)
  assert.match(page, /href=\{`\/prints\/\$\{print\.id\}`\}/)
  assert.match(page, /Print \{print\.id\}/)
  assert.match(page, /print\.language\?\.toUpperCase/)
})

test('wishlist radar compares target and current price only in the same currency', () => {
  const page = source('components/dashboard/DashboardPage.js')
  assert.match(page, /function WishlistRadarCard/)
  assert.match(page, /const currentCurrency = String\(price\?\.currency/)
  assert.match(page, /const targetCurrency = String\(item\?\.target_currency/)
  assert.match(page, /currentCurrency === targetCurrency/)
  assert.match(page, /currentValue <= targetValue/)
  assert.match(page, /Sin precio actual exacto/)
  assert.match(page, /Objetivo alcanzado/)
  assert.doesNotMatch(page, /exchangeRate|fxRate|convertCurrency/)
})

test('dashboard pulse uses verified game news and release readers instead of demo content', () => {
  const page = source('components/dashboard/DashboardPage.js')
  assert.match(page, /fetchReleasesByGame\(selectedGame, \{ limit: 3 \}\)/)
  assert.match(page, /fetchNewsByGame\(selectedGame, \{ limit: 3 \}\)/)
  assert.match(page, /Fuentes oficiales/)
  assert.match(page, /Próximos lanzamientos y noticias verificadas/)
  assert.doesNotMatch(page, /demoNews|mockNews|synthetic/i)
})

test('dashboard V2 stays responsive, keyboard-visible and reduced-motion safe', () => {
  const page = source('components/dashboard/DashboardPage.js')
  const css = source('components/dashboard/DashboardV2.css')
  assert.match(page, /import '\.\/DashboardV2\.css'/)
  assert.match(css, /\.v13-personal-grid/)
  assert.match(css, /\.v13-pulse-grid/)
  assert.match(css, /:focus-visible/)
  assert.match(css, /@media \(max-width: 760px\)/)
  assert.match(css, /@media \(max-width: 480px\)/)
  assert.match(css, /prefers-reduced-motion/)
})
