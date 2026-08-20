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

test('P0.7 Wishlist compares user target only with the current exact Print price', () => {
  const page = source('components/library/LibraryPage.js')
  assert.match(page, /item\?\.latest_price\?\.value/)
  assert.match(page, /item\?\.target_price/)
  assert.match(page, /currentCurrency === targetCurrency/)
  assert.match(page, /Objetivo alcanzado/)
  assert.match(page, /no aplicamos FX implícito/)
  assert.doesNotMatch(page, /known_value_eur.*wishlist/i)
})

test('P0.7 Wishlist edits priority and target through the existing exact print_id API', () => {
  const page = source('components/library/LibraryPage.js')
  const route = source('app/api/library/wishlist/route.js')
  const backend = repoSource('backend/app/routes/user_library.py')
  assert.match(page, /Editar objetivo y prioridad/)
  assert.match(page, /print_id: item\.print\.id/)
  assert.match(page, /priority: values\.priority/)
  assert.match(page, /target_price: values\.target_price/)
  assert.match(page, /target_currency: values\.target_currency/)
  assert.match(route, /callUserApi\('\/api\/v2\/me\/wishlist'/)
  assert.match(backend, /priority = int\(body\.get\("priority", 0\)\)/)
  assert.match(backend, /target_price = _decimal\(body\.get\("target_price"/)
})

test('P0.7 Wishlist exposes exact Print identity and truthful target states', () => {
  const page = source('components/library/LibraryPage.js')
  assert.match(page, /Print \{print\.id\}/)
  assert.match(page, /href=\{`\/prints\/\$\{print\.id\}`\}/)
  assert.match(page, /Precio actual exacto/)
  assert.match(page, /Sin precio actual/)
  assert.match(page, /No existe un precio Cardmarket actual para esta Print exacta/)
  assert.match(page, /Prioridad 3\/3/)
})

test('P0.7 Wishlist summary and sorting help decide what to pursue without creating a portfolio value', () => {
  const page = source('components/library/LibraryPage.js')
  assert.match(page, /Con precio actual/)
  assert.match(page, /Con objetivo/)
  assert.match(page, /Objetivo alcanzado/)
  assert.match(page, /priority_desc/)
  assert.match(page, /target_status/)
  assert.match(page, /Objetivo \/ cercanía/)
})

test('P0.7 Wishlist styling stays responsive and reduced-motion safe', () => {
  const page = source('components/library/LibraryPage.js')
  const css = source('components/library/LibraryWishlist.css')
  assert.match(page, /import '\.\/LibraryWishlist\.css'/)
  assert.match(css, /\.v11-wishlist-plan/)
  assert.match(css, /\.v11-target-status\.is-reached/)
  assert.match(css, /\.v11-target-editor/)
  assert.match(css, /@media \(max-width:760px\)/)
  assert.match(css, /@media \(prefers-reduced-motion:reduce\)/)
})
