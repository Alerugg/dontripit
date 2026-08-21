const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('loading and error states expose explicit assistive semantics', () => {
  const state = source('components/catalog/StatePanel.js')
  assert.match(state, /role=\{error \? 'alert' : loading \? 'status'/)
  assert.match(state, /aria-live=\{error \? 'assertive' : loading \? 'polite'/)
  assert.match(state, /aria-busy=\{loading \? 'true'/)
  assert.match(state, /aria-atomic=\{error \|\| loading \? 'true'/)
})

test('global accessibility supports skip navigation, high contrast and reduced motion', () => {
  const layout = source('app/layout.js')
  const css = source('app/accessibility.css')
  assert.match(layout, /className="dri-skip-link"/)
  assert.match(layout, /id="main-content" tabIndex=\{-1\}/)
  assert.match(css, /summary, \[tabindex\]/)
  assert.match(css, /@media \(prefers-contrast: more\)/)
  assert.match(css, /@media \(forced-colors: active\)/)
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/)
})

test('private account surfaces explicitly opt out of indexing', () => {
  for (const file of ['app/dashboard/page.js', 'app/collection/page.js', 'app/wishlist/page.js']) {
    const page = source(file)
    assert.match(page, /robots:\s*\{ index: false, follow: false \}/)
  }
})

test('catalog images are lazy and asynchronously decoded', () => {
  const image = source('components/common/FallbackImage.js')
  assert.match(image, /loading="lazy"/)
  assert.match(image, /decoding="async"/)
  assert.match(image, /role="img"/)
  assert.match(image, /aria-label=\{`Placeholder para/)
})

test('public SEO exposes sitemap while private surfaces stay disallowed', () => {
  const robots = source('app/robots.js')
  const sitemap = source('app/sitemap.js')
  assert.match(robots, /sitemap:\s*`\$\{siteUrl\}\/sitemap\.xml`/)
  for (const route of ['/dashboard', '/collection', '/wishlist']) assert.match(robots, new RegExp(route.replace('/', '\\/')))
  for (const route of ['/games/pokemon', '/games/magic', '/games/onepiece', '/games/yugioh']) assert.match(sitemap, new RegExp(route.replace('/', '\\/')))
})
