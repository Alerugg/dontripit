const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('federated all-results view does not block on the heavy normal-match search', () => {
  const route = source('app/api/search-v2/federated/route.js')

  assert.match(route, /const needsMatches = kind === 'matches'/)
  assert.doesNotMatch(route, /kind === 'all'\s*&&\s*page === 1[^\n]*needsMatches/)
  assert.match(route, /callInternalApi\('\/api\/v2\/search'/)
  assert.match(route, /Promise\.resolve\(skipped\(\{ items: \[\], total: null \}\)\)/)
})
