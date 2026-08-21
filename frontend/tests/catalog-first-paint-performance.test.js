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
  assert.match(route, /const \[cardsSource, printsSource, setsSource\] = await Promise\.all/)
})

test('explorer renders the first card page before requesting exact tab counts', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const client = source('lib/catalog/client.js')

  assert.match(explorer, /include_counts: deferCounts \? 0 : 1/)
  assert.match(explorer, /if \(!result\.counts_complete\)/)
  assert.match(explorer, /fetchCatalogCounts/)
  assert.match(explorer, /new AbortController\(\)/)
  assert.match(client, /counts_only: 1/)
  assert.match(client, /signal,/)
})

test('editorial home footer anchors point to real sections', () => {
  const home = source('components/home/PublicHome.js')
  const footer = source('components/layout/SiteFooter.js')

  assert.match(home, /id="how-it-works"/)
  assert.match(home, /id="games"/)
  assert.match(footer, /href="\/#how-it-works"/)
  assert.match(footer, /href="\/#games"/)
  assert.doesNotMatch(footer, /href="\/#releases"/)
})
