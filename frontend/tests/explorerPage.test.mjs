import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('home page is consolidated in app/page.js and composes the current home shell', async () => {
  const page = await fs.readFile(new URL('../app/page.js', import.meta.url), 'utf8')
  const shell = await fs.readFile(new URL('../components/home/HomePageShell.js', import.meta.url), 'utf8')
  const hero = await fs.readFile(new URL('../components/home/HomeHero.js', import.meta.url), 'utf8')
  const grid = await fs.readFile(new URL('../components/home/GameSpotlightGrid.js', import.meta.url), 'utf8')

  assert.match(page, /import HomePageShell from '\.\.\/components\/home\/HomePageShell'/)
  assert.match(page, /return <HomePageShell \/>/)
  assert.match(shell, /<TopNav \/>/)
  assert.match(shell, /className="page-shell home-shell home-shell-v3"/)
  assert.doesNotMatch(shell, /landing-shell|landing-v3/)
  assert.match(hero, /Explora cartas, sets y variantes/)
  assert.match(hero, /Pokémon, Magic, Yu-Gi-Oh!, One Piece y Riftbound/)
  assert.match(grid, /GAME_CATALOG\.map/)
})

test('legacy explorer redirects home while game routes choose the correct search experience', async () => {
  const explorerPage = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')
  const gamePage = await fs.readFile(new URL('../app/games/[slug]/page.js', import.meta.url), 'utf8')
  const gameExplorer = await fs.readFile(new URL('../components/games/GameExplorerPage.js', import.meta.url), 'utf8')
  const onePieceExplorer = await fs.readFile(new URL('../components/games/OnePieceExplorerV2Page.js', import.meta.url), 'utf8')
  const apiClient = await fs.readFile(new URL('../lib/catalog/client.js', import.meta.url), 'utf8')

  assert.match(explorerPage, /redirect\('\/'\)/)
  assert.match(gamePage, /import OnePieceExplorerV2Page/)
  assert.match(gamePage, /game\.slug === 'onepiece'/)
  assert.match(gamePage, /<OnePieceExplorerV2Page game=\{game\} \/>/)
  assert.match(gamePage, /<GameExplorerPage game=\{game\} \/>/)
  assert.match(onePieceExplorer, /<OnePieceSearchV2Experience game=\{game\} \/>/)

  // Legacy/current explorers for non-V2 games keep their existing singles path.
  assert.match(gameExplorer, /await fetchGamePrints\(\{/)
  assert.match(gameExplorer, /q: submittedQuery\.trim\(\)/)
  assert.match(apiClient, /game: toApiGameSlug\(filters\?\.game \|\| ''\)/)
  assert.match(gameExplorer, /sessionStorage\.getItem\(`scroll:/)
  assert.match(gameExplorer, /router\.replace\(/)
})

test('top nav links directly to dedicated TCG routes using the current logo navigation', async () => {
  const topNav = await fs.readFile(new URL('../components/layout/TopNav.js', import.meta.url), 'utf8')

  assert.match(topNav, /Don’tRipIt/)
  assert.match(topNav, /<Link href="\/" className="top-brand-logo-wrap"/)
  assert.match(topNav, /\{ href: '\/pokemon', label: 'Pokémon' \}/)
  assert.match(topNav, /\{ href: '\/magic', label: 'Magic' \}/)
  assert.match(topNav, /\{ href: '\/onepiece', label: 'One Piece' \}/)
  assert.doesNotMatch(topNav, /Admin Console|\/admin|\/console/)
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
  assert.match(games, /magic: 'mtg'/)
  assert.match(routes, /getGameExplorerHref\(slug\) \{\n  return getGameHref\(slug\)/)
})

test('Search V2 browser layer keeps backend credentials behind Next.js BFF routes', async () => {
  const normalRoute = await fs.readFile(new URL('../app/api/search-v2/route.js', import.meta.url), 'utf8')
  const suggestRoute = await fs.readFile(new URL('../app/api/search-v2/suggest/route.js', import.meta.url), 'utf8')
  const facetsRoute = await fs.readFile(new URL('../app/api/search-v2/facets/route.js', import.meta.url), 'utf8')
  const advancedRoute = await fs.readFile(new URL('../app/api/search-v2/advanced/route.js', import.meta.url), 'utf8')
  const client = await fs.readFile(new URL('../lib/searchV2/client.js', import.meta.url), 'utf8')

  assert.match(normalRoute, /callInternalApi\('\/api\/v2\/search'/)
  assert.match(suggestRoute, /callInternalApi\('\/api\/v2\/search\/suggest'/)
  assert.match(facetsRoute, /\/api\/v2\/games\/\$\{encodeURIComponent\(game\)\}\/facets/)
  assert.match(advancedRoute, /callInternalApi\('\/api\/v2\/search\/advanced'/)
  assert.match(client, /fetch\(`\/api\/search-v2/)
  assert.doesNotMatch(client, /INTERNAL_API_KEY|INTERNAL_API_BASE_URL/)
})

test('One Piece V2 exposes both normal and advanced search UX without changing other TCG explorers', async () => {
  const experience = await fs.readFile(new URL('../components/searchV2/OnePieceSearchV2Experience.js', import.meta.url), 'utf8')
  const advanced = await fs.readFile(new URL('../components/searchV2/AdvancedSearchPanel.js', import.meta.url), 'utf8')
  const results = await fs.readFile(new URL('../components/searchV2/SearchV2Results.js', import.meta.url), 'utf8')

  assert.match(experience, /suggestV2/)
  assert.match(experience, /searchV2/)
  assert.match(experience, /advancedSearchV2/)
  assert.match(experience, /Luffy OP05/)
  assert.match(experience, /monky lufi/)
  assert.match(advanced, /Identity|Advanced Search/)
  assert.match(advanced, /sv2-facet-groups/)
  assert.match(results, /variant_count/)
  assert.match(results, /Exact print/)
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
