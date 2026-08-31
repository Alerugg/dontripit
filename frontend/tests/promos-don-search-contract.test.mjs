import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

const read = (path) => fs.readFile(new URL(path, import.meta.url), 'utf8')

test('catalog BFF treats exact_identifier as authoritative Search V2 output', async () => {
  const route = await read('../app/api/catalog/search/route.js')
  assert.match(route, /paginationMode === 'exact_identifier'/)
  assert.match(route, /normalizeV2DirectItem/)
  assert.match(route, /enrichPrintsWithMarket\(selectedRows\)/)
  assert.match(route, /Falling back to \/api\/v1\/search[\s\S]*P-150/)
})

test('One Piece Explorer exposes dedicated source-owned DON search without mixing canonical cards', async () => {
  const explorer = await read('../components/catalog/CatalogExplorer.js')
  const client = await read('../lib/catalog/client.js')
  const donRoute = await read('../app/api/search-v2/don/route.js')
  const donSuggestRoute = await read('../app/api/search-v2/don/suggest/route.js')
  const donResults = await read('../components/catalog/DonMarketResults.js')

  assert.match(explorer, /Solo DON!!/)
  assert.match(explorer, /searchOnePieceDonPage/)
  assert.match(explorer, /suggestOnePieceDon/)
  assert.match(client, /\/api\/search-v2\/don/)
  assert.match(donRoute, /callInternalApi\('\/api\/v2\/search\/don'/)
  assert.match(donSuggestRoute, /callInternalApi\('\/api\/v2\/search\/don\/suggest'/)
  assert.match(donResults, /No inventamos una carta o Print canónico/)
  assert.doesNotMatch(donResults, /href=\{`\/prints\//)
})

test('DON URL state is only restorable inside One Piece', async () => {
  const gamePage = await read('../app/games/[slug]/page.js')
  assert.match(gamePage, /donOnly: game\.slug === 'onepiece' && query\?\.don === '1'/)
})
