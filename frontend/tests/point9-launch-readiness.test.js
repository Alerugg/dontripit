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
