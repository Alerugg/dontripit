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

test('top nav keeps public links and isolated admin entrypoint', async () => {
  const topNav = await fs.readFile(new URL('../components/layout/TopNav.js', import.meta.url), 'utf8')

  assert.match(topNav, /<Link href="\/" className="top-link">Explorer<\/Link>/)
  assert.match(topNav, /<span className="top-link disabled">Colección<\/span>/)
  assert.match(topNav, /<span className="top-link disabled">Wishlist<\/span>/)
  assert.match(topNav, /<span className="top-link disabled">Marketplace<\/span>/)

  assert.match(topNav, /<Link href="\/admin\/api-console" className="admin-link">Admin Console<\/Link>/)
})

test('catalog client consumes internal BFF routes', async () => {
  const apiClient = await fs.readFile(new URL('../lib/catalog/client.js', import.meta.url), 'utf8')

  assert.match(apiClient, /\/api\/catalog\/search/)
  assert.match(apiClient, /\/api\/catalog\/cards\//)
  assert.match(apiClient, /\/api\/catalog\/prints\//)
  assert.doesNotMatch(apiClient, /NEXT_PUBLIC_API_KEY/)
})

test('BFF helper reads internal server-side env vars', async () => {
  const internalApi = await fs.readFile(new URL('../lib/catalog/internalApi.js', import.meta.url), 'utf8')

  assert.match(internalApi, /INTERNAL_API_BASE_URL/)
  assert.match(internalApi, /INTERNAL_API_KEY/)
})
