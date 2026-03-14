import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('home page is a landing and links to explorer', async () => {
  const page = await fs.readFile(new URL('../app/page.js', import.meta.url), 'utf8')

  assert.match(page, /Explorar catálogo/)
  assert.match(page, /Browse catalog/)
  assert.match(page, /href="\/explorer"/)
  assert.doesNotMatch(page, /CatalogSidebar/)
  assert.doesNotMatch(page, /searchCatalog\(/)
})

test('explorer page uses explicit search submit + inline suggestions', async () => {
  const explorerPage = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')

  assert.match(explorerPage, /const \[inputValue, setInputValue\] = useState\(''\)/)
  assert.match(explorerPage, /const \[submittedQuery, setSubmittedQuery\] = useState\(''\)/)
  assert.match(explorerPage, /useDebouncedValue\(inputValue\.trim\(\), 250\)/)
  assert.match(explorerPage, /suggestCatalog\(\{ q: debouncedInput, game, limit: 8 \}\)/)
  assert.match(explorerPage, /searchCatalog\(\{ q: submittedQuery, game, type, limit: 36, offset: 0 \}\)/)
  assert.match(explorerPage, /onSubmitSearch=\{handleSubmitSearch\}/)
})

test('top nav keeps branding and home + explorer links', async () => {
  const topNav = await fs.readFile(new URL('../components/layout/TopNav.js', import.meta.url), 'utf8')

  assert.match(topNav, /Don’tRipIt/)
  assert.match(topNav, /<Link href="\/" className="top-link">Home<\/Link>/)
  assert.match(topNav, /<Link href="\/explorer" className="top-link">Explorer<\/Link>/)
  assert.match(topNav, /<span className="top-link disabled">Colección<\/span>/)
  assert.match(topNav, /<span className="top-link disabled">Wishlist<\/span>/)
  assert.match(topNav, /<span className="top-link disabled">Marketplace<\/span>/)

  assert.match(topNav, /<Link href="\/admin\/api-console" className="admin-link">Admin Console<\/Link>/)
})

test('catalog client consumes internal BFF routes', async () => {
  const apiClient = await fs.readFile(new URL('../lib/catalog/client.js', import.meta.url), 'utf8')

  assert.match(apiClient, /\/api\/catalog\/search/)
  assert.match(apiClient, /\/api\/catalog\/suggest/)
  assert.match(apiClient, /\/api\/catalog\/cards\//)
  assert.match(apiClient, /\/api\/catalog\/prints\//)
  assert.doesNotMatch(apiClient, /NEXT_PUBLIC_API_KEY/)
  assert.match(apiClient, /\{ value: 'riftbound', label: 'Riftbound' \}/)
})

test('BFF helper reads internal server-side env vars', async () => {
  const internalApi = await fs.readFile(new URL('../lib/catalog/internalApi.js', import.meta.url), 'utf8')

  assert.match(internalApi, /INTERNAL_API_BASE_URL/)
  assert.match(internalApi, /INTERNAL_API_KEY/)
})


test("layout metadata uses Don’tRipIt branding", async () => {
  const layout = await fs.readFile(new URL('../app/layout.js', import.meta.url), 'utf8')

  assert.match(layout, /Don’tRipIt/)
})

test('catalog search BFF forwards dynamic q without hardcoded demo query', async () => {
  const routeSource = await fs.readFile(new URL('../app/api/catalog/search/route.js', import.meta.url), 'utf8')

  assert.match(routeSource, /q: searchParams\.get\('q'\) \|\| ''/)
  assert.doesNotMatch(routeSource, /charizard/i)
})
