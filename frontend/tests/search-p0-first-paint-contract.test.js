const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('canonical card first paint does not wait for exhaustive print and set counts', () => {
  const route = source('app/api/catalog/search/route.js')
  assert.match(route, /const includeCounts = searchParams\.get\('include_counts'\) !== '0'/)
  assert.match(route, /const fastCardPage = !includeCounts/)
  assert.match(route, /if \(fastCardPage\)/)
  assert.match(route, /counts_complete: false/)
  assert.match(route, /print: null/)
  assert.match(route, /set: null/)
})

test('explorer renders card results before requesting exhaustive tab counts', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const client = source('lib/catalog/client.js')

  assert.match(explorer, /include_counts: deferCounts \? 0 : 1/)
  assert.match(explorer, /if \(!result\.counts_complete\)/)
  assert.match(explorer, /fetchCatalogCounts/)
  assert.match(explorer, /new AbortController\(\)/)
  assert.match(client, /counts_only: 1/)
  assert.match(client, /signal,/)
})