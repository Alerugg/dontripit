const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.join(__dirname, '..')
const client = fs.readFileSync(path.join(root, 'lib/catalog/client.js'), 'utf8')
const marketCss = fs.readFileSync(path.join(root, 'app/price-first-market.css'), 'utf8')
const proxy = fs.readFileSync(path.join(root, 'proxy.js'), 'utf8')

test('interactive catalog requests fail closed instead of spinning forever', () => {
  assert.match(client, /const SEARCH_TIMEOUT_MS = 15000/)
  assert.match(client, /const SUGGEST_TIMEOUT_MS = 8000/)
  assert.match(client, /payload === null/)
  assert.match(client, /timeoutError\.name = 'TimeoutError'/)
  assert.match(client, /timeoutMs: options\.timeoutMs \?\? SEARCH_TIMEOUT_MS/)
})

test('mobile Cardmarket window keeps all three metrics balanced', () => {
  assert.match(marketCss, /\.v15-price-window-grid/)
  assert.match(marketCss, /grid-template-columns: repeat\(3, minmax\(0, 1fr\)\) !important;/)
  assert.match(marketCss, /\.v15-print-market-primary > small/)
  assert.match(marketCss, /font-size: \.64rem;/)
})

test('all footer legal policies remain public while account surfaces stay protected', () => {
  assert.match(proxy, /'\/privacy'/)
  assert.match(proxy, /'\/cookies'/)
  assert.match(proxy, /'\/terms'/)
  assert.doesNotMatch(proxy, /'\/dashboard'/)
  assert.doesNotMatch(proxy, /'\/collection'/)
  assert.doesNotMatch(proxy, /'\/wishlist'/)
})
