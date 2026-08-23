const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.join(__dirname, '..')
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8')

const searchV2Client = read('lib/searchV2/client.js')
const homeSearch = read('components/home/HomeSearch.js')
const searchExperience = read('components/searchV2/OnePieceSearchV2Experience.js')
const catalogExplorer = read('components/catalog/CatalogExplorer.js')

test('search clients accept AbortSignal instead of leaving stale requests alive', () => {
  assert.match(searchV2Client, /signal:\s*options\.signal/)
  assert.match(searchV2Client, /export async function suggestV2\([\s\S]*options = \{\}/)
  assert.match(searchV2Client, /export async function federatedSearchV2\([\s\S]*options = \{\}/)
  assert.match(searchV2Client, /export async function advancedSearchV2\([\s\S]*options = \{\}/)
})

test('home autocomplete aborts the previous request on query or scope changes', () => {
  assert.match(homeSearch, /const controller = new AbortController\(\)/)
  assert.match(homeSearch, /suggestCatalog\([\s\S]*signal: controller\.signal/)
  assert.match(homeSearch, /controller\.abort\(\)/)
  assert.match(homeSearch, /requestError\?\.name !== 'AbortError'/)
})

test('Search V2 normal, suggestion and advanced lanes are latest-request-wins', () => {
  assert.match(searchExperience, /fetchFacetsV2\(game\.slug, \{ signal: controller\.signal \}\)/)
  assert.match(searchExperience, /suggestV2\([\s\S]*signal: controller\.signal/)
  assert.match(searchExperience, /federatedSearchV2\([\s\S]*signal: controller\.signal/)
  assert.match(searchExperience, /advancedControllerRef\.current\?\.abort\(\)/)
  assert.match(searchExperience, /advancedSearchV2\([\s\S]*signal: controller\.signal/)
  assert.match(searchExperience, /advancedControllerRef\.current !== controller/)
})

test('canonical explorer keeps its existing abort-safe search and count pipeline', () => {
  assert.match(catalogExplorer, /const controller = new AbortController\(\)/)
  assert.match(catalogExplorer, /suggestCatalog\([\s\S]*signal: controller\.signal/)
  assert.match(catalogExplorer, /fetchCatalogCounts\(filters, \{ signal: controller\.signal \}\)/)
  assert.match(catalogExplorer, /controller\.abort\(\)/)
})
