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

test('point 9 launch hides newsletter opt-in', () => {
  const guard = source('app/register/layout.js')
  assert.match(guard, /marketing_consent/)
  assert.match(guard, /display: none/)
})
