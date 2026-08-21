const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('point 9 launch legal contact is public and consistent', () => {
  for (const file of ['app/privacy/page.js', 'app/cookies/page.js', 'app/terms/page.js', 'components/layout/SiteFooter.js']) {
    assert.match(source(file), /info@dontripit\.com/)
  }
})

test('point 9 launch uses technical cookies only', () => {
  const cookies = source('app/cookies/page.js')
  const pkg = source('package.json')
  assert.match(cookies, /estrictamente necesaria/)
  assert.match(cookies, /No utilizamos cookies publicitarias ni cookies de analítica/)
  assert.doesNotMatch(pkg, /google-analytics|gtag|posthog|plausible|segment/i)
})

test('point 9 SEO and accessibility baseline is present', () => {
  const layout = source('app/layout.js')
  const robots = source('app/robots.js')
  const sitemap = source('app/sitemap.js')
  const accessibility = source('app/accessibility.css')
  assert.match(layout, /metadataBase/)
  assert.match(layout, /dri-skip-link/)
  assert.match(robots, /\/dashboard/)
  assert.match(robots, /\/collection/)
  assert.match(sitemap, /\/privacy/)
  assert.match(sitemap, /\/cookies/)
  assert.match(sitemap, /\/terms/)
  assert.match(accessibility, /prefers-reduced-motion/)
  assert.match(accessibility, /focus-visible/)
})

test('point 10 removes newsletter consent from launch registration', () => {
  const auth = source('components/auth/AuthShell.js')
  const registerLayout = source('app/register/layout.js')
  assert.doesNotMatch(auth, /marketing_consent|Quiero recibir novedades/i)
  assert.doesNotMatch(registerLayout, /marketing_consent|display:\s*none/i)
})

test('point 10 keeps all auth entry routes out of search indexes', () => {
  for (const file of ['app/login/layout.js', 'app/register/layout.js', 'app/forgot-password/layout.js', 'app/reset-password/layout.js']) {
    const route = source(file)
    assert.match(route, /index:\s*false/)
    assert.match(route, /follow:\s*false/)
  }
})

test('point 10 account deletion is authenticated, explicit and cascades user-owned data', () => {
  const bff = source('app/api/auth/me/route.js')
  const backend = source('../backend/app/routes/user_auth.py')
  const models = source('../backend/app/user_models.py')
  assert.match(bff, /export async function DELETE/)
  assert.match(bff, /\/api\/v2\/auth\/account/)
  assert.match(bff, /clearSessionCookie/)
  assert.match(backend, /@user_auth_bp\.delete\("\/api\/v2\/auth\/account"\)/)
  assert.match(backend, /confirmation != "ELIMINAR"/)
  assert.match(backend, /password_matches\(user, password\)/)
  assert.match(backend, /session\.delete\(user\)/)
  assert.ok((models.match(/ondelete="CASCADE"/g) || []).length >= 4)
})

test('account deletion is available in the member UI with deliberate confirmation', () => {
  const dashboard = source('components/dashboard/DashboardPage.js')
  const css = source('components/dashboard/DashboardPage.css')
  assert.match(dashboard, /Eliminar mi cuenta/)
  assert.match(dashboard, /confirmation:\s*deleteConfirmation/)
  assert.match(dashboard, /toUpperCase\(\) === 'ELIMINAR'/)
  assert.match(dashboard, /autoComplete="current-password"/)
  assert.match(dashboard, /method:\s*'DELETE'/)
  assert.match(dashboard, /disabled={!canDeleteAccount}/)
  assert.match(css, /\.ux-delete-account/)
  assert.match(css, /@media \(max-width: 680px\)/)
})

test('card and exact-print routes expose dynamic canonical metadata without duplicate brand suffixes', () => {
  for (const file of ['app/cards/[id]/layout.js', 'app/prints/[id]/layout.js']) {
    const layout = source(file)
    assert.match(layout, /generateMetadata/)
    assert.match(layout, /callInternalApi/)
    assert.match(layout, /alternates:\s*{ canonical }/)
    assert.match(layout, /Don’tRipIt/)
    assert.doesNotMatch(layout, /const title = .*Don’tRipIt/)
  }
  assert.match(source('app/prints/[id]/layout.js'), /collector_number/)
})

test('legacy card route never points users at a guessed game', () => {
  const page = source('app/cards/[id]/page.js')
  assert.match(page, /callInternalApi\(`\/api\/v1\/cards\/\$\{encodeURIComponent\(cardId\)\}`\)/)
  assert.match(page, /normalizeGameSlug\(upstream\.payload\?\.game_slug \|\| upstream\.payload\?\.game \|\| ''\)/)
  assert.match(page, /redirect\(getCardHref\(gameSlug, cardId\)\)/)
  assert.match(page, /redirect\(explorerFallback\(cardId\)\)/)
  assert.doesNotMatch(page, /card\?\.game \|\| 'pokemon'/)
  assert.doesNotMatch(page, /getCardHref\('pokemon'/)
})

test('federated result tabs use explicit zero counts, readable active state and mobile scrolling', () => {
  const results = source('components/searchV2/FederatedSearchResults.js')
  const css = source('components/searchV2/FederatedSearchResults.css')
  assert.match(results, /\['singles', 'Cartas', singlesCount\]/)
  assert.match(results, /\['sets', 'Colecciones', setsCount\]/)
  assert.match(results, /const sealedCount = safeCount/)
  assert.match(results, /const matchesCount = safeCount/)
  assert.doesNotMatch(results, /\['singles', 'Singles'/)
  assert.doesNotMatch(results, /return '—'/)
  assert.match(css, /\.fsr-tab\.is-active/)
  assert.match(css, /color: #17131f/)
  assert.match(css, /\.fsr-tab:focus-visible/)
  assert.match(css, /overflow-x: auto/)
  assert.match(css, /white-space: nowrap/)
})

test('collector-first home stays truthful and does not import mock prices', () => {
  const home = source('components/home/PublicHome.js')
  const story = source('components/home/HomeIdentityStoryV3.js')
  assert.match(home, /GAME_CATALOG/)
  assert.match(home, /activeGames\.map/)
  assert.match(home, /impresión física exacta/)
  assert.match(home, /correspondencia es segura/)
  assert.match(home, /Sin mapeo exacto no mostramos precio/)
  assert.match(home, /Sin precio inventado/)
  assert.match(home, /Fuente y procedencia visibles/)
  assert.match(home, /SiteFooter/)
  assert.match(story, /Correspondencia exacta/)
  assert.match(story, /Sin precio seguro/)
  assert.doesNotMatch(home, /312,40|68,95|3\.240|12480|Pikachu · Reverse/)
})

test('launch front has explicit responsive layouts for home, game hubs and dashboard', () => {
  const home = source('app/canva-workspace.css')
  const hero = source('app/lovable-v2-hero.css')
  const homeV3 = source('app/lovable-v3-home.css')
  const hub = source('components/games/GameExplorerPage.css')
  const dashboard = source('components/dashboard/DashboardPage.css')
  assert.match(home, /@media \(max-width: 860px\)/)
  assert.match(home, /@media \(max-width: 680px\)/)
  assert.match(home, /@media \(max-width: 430px\)/)
  assert.match(home, /prefers-reduced-motion/)
  assert.match(hero, /@media \(max-width: 980px\)/)
  assert.match(hero, /@media \(max-width: 620px\)/)
  assert.match(hero, /prefers-reduced-motion/)
  assert.match(homeV3, /@media \(max-width: 760px\)/)
  assert.match(homeV3, /@media \(max-width: 470px\)/)
  assert.match(homeV3, /prefers-reduced-motion/)
  assert.match(hub, /@media \(max-width: 720px\)/)
  assert.match(hub, /overflow-x: auto/)
  assert.match(dashboard, /@media \(max-width: 680px\)/)
  assert.match(dashboard, /@media \(max-width: 440px\)/)
})

test('home game selector remains keyboard and screen-reader explicit', () => {
  const search = source('components/home/HomeSearch.js')
  assert.match(search, /aria-pressed/)
  assert.match(search, /aria-label=/)
  assert.match(search, /type="search"/)
  assert.match(search, /data-game=/)
})
