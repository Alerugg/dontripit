import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('home page exposes multi-game explorer UI pieces', async () => {
  const page = await fs.readFile(new URL('../app/page.js', import.meta.url), 'utf8')

  assert.match(page, /TCG Multi-Game Explorer/)
  assert.match(page, /SearchControls/)
  assert.match(page, /ResultsGrid/)
  assert.match(page, /searchCatalog\(/)
})

test('api client uses NEXT_PUBLIC_API_BASE_URL and v1 endpoints', async () => {
  const apiClient = await fs.readFile(new URL('../lib/apiClient.js', import.meta.url), 'utf8')

  assert.match(apiClient, /NEXT_PUBLIC_API_BASE_URL/)
  assert.match(apiClient, /\/api\/v1\/search/)
  assert.match(apiClient, /\/api\/v1\/cards\//)
  assert.match(apiClient, /\/api\/v1\/prints\//)
})

test('detail routes exist for cards and prints', async () => {
  const cardDetail = await fs.readFile(new URL('../app/cards/[id]/page.js', import.meta.url), 'utf8')
  const printDetail = await fs.readFile(new URL('../app/prints/[id]/page.js', import.meta.url), 'utf8')

  assert.match(cardDetail, /fetchCardById/)
  assert.match(printDetail, /fetchPrintById/)
})
