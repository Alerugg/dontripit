const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.join(__dirname, '..')
const explorerPage = fs.readFileSync(path.join(root, 'app/explorer/page.js'), 'utf8')
const explorer = fs.readFileSync(path.join(root, 'components/catalog/CatalogExplorer.js'), 'utf8')
const client = fs.readFileSync(path.join(root, 'lib/catalog/client.js'), 'utf8')
const nextConfig = fs.readFileSync(path.join(root, 'next.config.js'), 'utf8')

test('query-only Explorer enters canonical Card mode before expensive federated counts', () => {
  assert.match(explorerPage, /function initialResultType\(params, query\)/)
  assert.match(explorerPage, /return query \? 'card' : ''/)
  assert.match(explorerPage, /initialType=\{kind\}/)
  assert.match(explorer, /const deferCounts = type === 'card' && sort === 'relevance'/)
  assert.match(explorer, /include_counts: deferCounts \? 0 : 1/)
  assert.match(explorer, /fetchCatalogCounts\(filters/)
})

test('cold first canonical Card page gets enough time without relaxing every catalog request', () => {
  assert.match(client, /const SEARCH_TIMEOUT_MS = 15000/)
  assert.match(client, /const FIRST_CARD_PAGE_TIMEOUT_MS = 30000/)
  assert.match(client, /filters\?\.type === 'card'/)
  assert.match(client, /Number\(filters\?\.include_counts\) === 0/)
  assert.match(client, /Number\(filters\?\.offset \|\| 0\) === 0/)
  assert.match(client, /firstCanonicalCardPage \? FIRST_CARD_PAGE_TIMEOUT_MS : SEARCH_TIMEOUT_MS/)
})

test('frontend CSP and Next image policy allow the Don’tRipIt media proxy', () => {
  assert.match(nextConfig, /hostname: 'api\.dontripit\.com'/)
  assert.match(nextConfig, /img-src[^\n]*https:\/\/api\.dontripit\.com/)
  assert.match(nextConfig, /connect-src 'self' https:\/\/api\.dontripit\.com/)
})
