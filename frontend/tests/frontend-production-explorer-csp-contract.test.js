const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.join(__dirname, '..')
const explorerPage = fs.readFileSync(path.join(root, 'app/explorer/page.js'), 'utf8')
const explorer = fs.readFileSync(path.join(root, 'components/catalog/CatalogExplorer.js'), 'utf8')
const nextConfig = fs.readFileSync(path.join(root, 'next.config.js'), 'utf8')

test('query-only Explorer enters canonical Card mode before expensive federated counts', () => {
  assert.match(explorerPage, /function initialResultType\(params, query\)/)
  assert.match(explorerPage, /return query \? 'card' : ''/)
  assert.match(explorerPage, /initialType=\{kind\}/)
  assert.match(explorer, /const deferCounts = type === 'card' && sort === 'relevance'/)
  assert.match(explorer, /include_counts: deferCounts \? 0 : 1/)
  assert.match(explorer, /fetchCatalogCounts\(filters/)
})

test('frontend CSP and Next image policy allow the Don’tRipIt media proxy', () => {
  assert.match(nextConfig, /hostname: 'api\.dontripit\.com'/)
  assert.match(nextConfig, /img-src[^\n]*https:\/\/api\.dontripit\.com/)
  assert.match(nextConfig, /connect-src 'self' https:\/\/api\.dontripit\.com/)
})
