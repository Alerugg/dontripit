import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('home page composes the new modular Home V2 experience', async () => {
  const page = await fs.readFile(new URL('../app/page.js', import.meta.url), 'utf8')
  const hero = await fs.readFile(new URL('../components/home/HomeHero.js', import.meta.url), 'utf8')
  const metrics = await fs.readFile(new URL('../components/home/HomeMetrics.js', import.meta.url), 'utf8')
  const gameGrid = await fs.readFile(new URL('../components/home/HomeGameGrid.js', import.meta.url), 'utf8')
  const blueprint = await fs.readFile(new URL('../components/home/HomeBlueprint.js', import.meta.url), 'utf8')
  const why = await fs.readFile(new URL('../components/home/HomeWhySection.js', import.meta.url), 'utf8')
  const finalCta = await fs.readFile(new URL('../components/home/HomeFinalCta.js', import.meta.url), 'utf8')

  assert.match(page, /<HomeHero \/>/)
  assert.match(page, /<HomeMetrics \/>/)
  assert.match(page, /<HomeGameGrid \/>/)
  assert.match(page, /<HomeBlueprint \/>/)
  assert.match(page, /<HomeWhySection \/>/)
  assert.match(page, /<HomeFinalCta \/>/)
  assert.doesNotMatch(page, /CatalogExplorer/)
  assert.match(hero, /HOME V2 REAL/)
  assert.match(metrics, /home-metrics-band/)
  assert.match(gameGrid, /home-game-grid/)
  assert.match(blueprint, /home-blueprint-grid/)
  assert.match(why, /home-why-cards/)
  assert.match(finalCta, /home-final-actions/)
})

test('global explorer delegates search behavior to reusable catalog explorer', async () => {
  const explorerPage = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')
  const explorerComponent = await fs.readFile(new URL('../components/catalog/CatalogExplorer.js', import.meta.url), 'utf8')

  assert.match(explorerPage, /<CatalogExplorer/)
  assert.match(explorerPage, /heading="Explorador global"/)
  assert.match(explorerComponent, /const \[inputValue, setInputValue\] = useState\(''\)/)
  assert.match(explorerComponent, /const \[submittedQuery, setSubmittedQuery\] = useState\(''\)/)
  assert.match(explorerComponent, /useDebouncedValue\(inputValue\.trim\(\), 220\)/)
  assert.match(explorerComponent, /suggestCatalog\(\{ q: debouncedInput, game: scopedGame \|\| game, limit: 8 \}\)/)
  assert.match(explorerComponent, /searchCatalog\(\{ q: submittedQuery, game: scopedGame \|\| game, type, limit: 36, offset: 0 \}\)/)
})

test('top nav keeps branding, games entry point, and critical navigation links', async () => {
  const topNav = await fs.readFile(new URL('../components/layout/TopNav.js', import.meta.url), 'utf8')

  assert.match(topNav, /Don’tRipIt/)
  assert.match(topNav, /<Link href="\/" className="top-link">Home<\/Link>/)
  assert.match(topNav, /<Link href="\/games\/pokemon" className="top-link">Juegos<\/Link>/)
  assert.match(topNav, /<Link href="\/explorer" className="top-link">Explorar todo<\/Link>/)
  assert.match(topNav, /<span className="top-link disabled">Colección<\/span>/)
  assert.match(topNav, /<span className="top-link disabled">Wishlist<\/span>/)
  assert.match(topNav, /<Link href="\/admin\/api-console" className="admin-link">Admin Console<\/Link>/)
})

test('catalog client keeps BFF routes while game catalog is defined separately', async () => {
  const apiClient = await fs.readFile(new URL('../lib/catalog/client.js', import.meta.url), 'utf8')
  const games = await fs.readFile(new URL('../lib/catalog/games.js', import.meta.url), 'utf8')

  assert.match(apiClient, /\/api\/catalog\/search/)
  assert.match(apiClient, /\/api\/catalog\/suggest/)
  assert.match(apiClient, /\/api\/catalog\/cards\//)
  assert.match(apiClient, /\/api\/catalog\/prints\//)
  assert.doesNotMatch(apiClient, /NEXT_PUBLIC_API_KEY/)
  assert.match(games, /slug: 'riftbound'/)
  assert.match(games, /GAME_OPTIONS = \[/)
  assert.match(games, /slug === 'one-piece' \? 'onepiece' : slug/)
})

test('legacy tcg and play entry points redirect to the scoped games explorer', async () => {
  const tcgRoute = await fs.readFile(new URL('../app/tcg/[slug]/page.js', import.meta.url), 'utf8')
  const playRoute = await fs.readFile(new URL('../app/play/[slug]/page.js', import.meta.url), 'utf8')

  assert.match(tcgRoute, /redirect\(`\/games\/\$\{params\.slug\}\/explorer`\)/)
  assert.match(playRoute, /redirect\(`\/games\/\$\{params\.slug\}\/explorer`\)/)
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
