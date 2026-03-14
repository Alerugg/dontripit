import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('home page exposes catalog explorer structure', async () => {
  const page = await fs.readFile(new URL('../app/page.js', import.meta.url), 'utf8')

  assert.match(page, /Explorer público multi-game/)
  assert.match(page, /CatalogSidebar/)
  assert.match(page, /CatalogResults/)
  assert.match(page, /searchCatalog\(/)
})

test('top nav does not expose admin console link in public nav', async () => {
  const topNav = await fs.readFile(new URL('../components/layout/TopNav.js', import.meta.url), 'utf8')

  assert.doesNotMatch(topNav, /Admin Console/)
  assert.match(topNav, /Explorer/)
})

test('catalog client consumes internal BFF routes', async () => {
  const apiClient = await fs.readFile(new URL('../lib/catalog/client.js', import.meta.url), 'utf8')

  assert.match(apiClient, /\/api\/catalog\/search/)
  assert.match(apiClient, /\/api\/catalog\/cards\//)
  assert.match(apiClient, /\/api\/catalog\/prints\//)
  assert.doesNotMatch(apiClient, /NEXT_PUBLIC_API_KEY/)
})

test('BFF routes read internal server-side env vars', async () => {
  const internalApi = await fs.readFile(new URL('../lib/catalog/internalApi.js', import.meta.url), 'utf8')

  assert.match(internalApi, /INTERNAL_API_BASE_URL/)
  assert.match(internalApi, /INTERNAL_API_KEY/)
})
