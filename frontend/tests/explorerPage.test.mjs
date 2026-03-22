import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('home page uses the new dedicated TCG landing structure', async () => {
  const page = await fs.readFile(new URL('../app/page.js', import.meta.url), 'utf8')
  const metrics = await fs.readFile(new URL('../components/home/HomeMetrics.js', import.meta.url), 'utf8')

  assert.match(page, /<TopNav \/>/)
  assert.match(page, /Explora cartas y sellado por TCG\. Una carta por resultado\. Variantes dentro\./)
  assert.match(page, /<HomeMetrics metrics=\{metrics\} \/>/)
  assert.match(page, /GAME_CATALOG\.map/)
  assert.match(page, /href="\/pokemon" className="primary-btn"/)
  assert.doesNotMatch(page, /href="\/explorer"/)
  assert.match(metrics, /function HomeMetrics\(\{ metrics = \[\] \}\)/)
})

test('legacy explorer redirects to home while the game page owns search UX', async () => {
  const explorerPage = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')
  const gamePage = await fs.readFile(new URL('../app/games/[slug]/page.js', import.meta.url), 'utf8')
  const gameExplorer = await fs.readFile(new URL('../components/games/GameExplorerPage.js', import.meta.url), 'utf8')

  assert.match(explorerPage, /redirect\('\/'\)/)
  assert.match(gamePage, /<GameExplorerPage game=\{game\} \/>/)
  assert.match(gameExplorer, /searchCatalog\(\{ q: submittedQuery\.trim\(\), game: game\.slug, type: 'card'/)
  assert.match(gameExplorer, /sessionStorage\.getItem\(`scroll:/)
  assert.match(gameExplorer, /router\.replace\(nextParams\.toString\(\) \? `\$\{pathname\}\?\$\{nextParams\.toString\(\)\}` : pathname, \{ scroll: false \}\)/)
})

test('top nav links directly to dedicated TCG routes', async () => {
  const topNav = await fs.readFile(new URL('../components/layout/TopNav.js', import.meta.url), 'utf8')

  assert.match(topNav, /Don’tRipIt/)
  assert.match(topNav, /<Link href="\/" className="top-link">Home<\/Link>/)
  assert.match(topNav, /\{ href: '\/pokemon', label: 'Pokémon' \}/)
  assert.match(topNav, /\{ href: '\/magic', label: 'Magic' \}/)
  assert.match(topNav, /<Link href="\/admin\/api-console" className="admin-link">Admin Console<\/Link>/)
  assert.doesNotMatch(topNav, /\/explorer/)
})

test('catalog client keeps BFF routes while game catalog normalizes new slugs', async () => {
  const apiClient = await fs.readFile(new URL('../lib/catalog/client.js', import.meta.url), 'utf8')
  const games = await fs.readFile(new URL('../lib/catalog/games.js', import.meta.url), 'utf8')
  const routes = await fs.readFile(new URL('../lib/catalog/routes.js', import.meta.url), 'utf8')

  assert.match(apiClient, /\/api\/catalog\/search/)
  assert.match(apiClient, /\/api\/catalog\/suggest/)
  assert.match(apiClient, /\/api\/catalog\/cards\//)
  assert.match(apiClient, /\/api\/catalog\/prints\//)
  assert.match(games, /mtg: 'magic'/)
  assert.match(games, /'one-piece': 'onepiece'/)
  assert.match(routes, /getGameExplorerHref\(slug\) \{\n  return getGameHref\(slug\)/)
})

test('legacy tcg and play entry points redirect to the scoped game page', async () => {
  const tcgRoute = await fs.readFile(new URL('../app/tcg/[slug]/page.js', import.meta.url), 'utf8')
  const playRoute = await fs.readFile(new URL('../app/play/[slug]/page.js', import.meta.url), 'utf8')

  assert.match(tcgRoute, /redirect\(`\/games\/\$\{params\.slug\}`\)/)
  assert.match(playRoute, /redirect\(`\/games\/\$\{params\.slug\}`\)/)
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
