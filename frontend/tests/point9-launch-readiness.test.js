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

test('collector-first home stays truthful and does not import mock prices', () => {
  const home = source('components/home/PublicHome.js')
  assert.match(home, /la versión correcta/)
  assert.match(home, /activeGames\.length/)
  assert.match(home, /Cardmarket/)
  assert.match(home, /Sin fechas inventadas/)
  assert.match(home, /SiteFooter/)
  assert.doesNotMatch(home, /312,40|68,95|3\.240|12480|Pikachu · Reverse/)
})

test('launch front has explicit responsive layouts for home, game hubs and dashboard', () => {
  const home = source('app/canva-workspace.css')
  const hub = source('components/games/GameExplorerPage.css')
  const dashboard = source('components/dashboard/DashboardPage.css')
  assert.match(home, /@media \(max-width: 860px\)/)
  assert.match(home, /@media \(max-width: 680px\)/)
  assert.match(home, /@media \(max-width: 430px\)/)
  assert.match(home, /prefers-reduced-motion/)
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
