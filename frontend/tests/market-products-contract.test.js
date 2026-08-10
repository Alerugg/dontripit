const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('sealed market proxy uses the source-owned backend catalog', () => {
  const route = source('app/api/catalog/market-products/route.js')
  const client = source('lib/catalog/client.js')
  assert.match(route, /\/api\/v1\/market\/products/)
  assert.match(route, /group:\s*'non_single'/)
  assert.match(client, /fetchMarketProductsByGame/)
  assert.match(client, /\/api\/catalog\/market-products/)
})

test('sealed shelf keeps listing evidence separate from canonical identity', () => {
  const shelf = source('components/games/MarketProductShelf.js')
  assert.match(shelf, /listing_status === 'available_verified'/)
  assert.match(shelf, /identity_status === 'verified'/)
  assert.match(shelf, /En catálogo Cardmarket/)
  assert.match(shelf, /Versión identificada en Don’tRipIt/)
  assert.match(shelf, /Identidad interna pendiente/)
  assert.match(shelf, /https:\/\/www\.cardmarket\.com/)
  assert.doesNotMatch(shelf, /No listado/)
})
