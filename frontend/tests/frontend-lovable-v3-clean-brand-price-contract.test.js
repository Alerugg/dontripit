const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')

test('loads the clean interior override after the approved Home styles', () => {
  const layout = read('app/layout.js')
  const homeIndex = layout.indexOf("import './lovable-v4-home-media.css'")
  const cleanIndex = layout.indexOf("import './lovable-v3-interior-clean.css'")
  assert.ok(homeIndex >= 0)
  assert.ok(cleanIndex > homeIndex)
})

test('uses official Don’tRipIt assets for wordmark and navigation mark', () => {
  const brand = read('components/brand/BrandMark.js')
  assert.match(brand, /\/branding\/dontripit-wordmark\.png/)
  assert.match(brand, /\/branding\/dontripit-nav-mark\.png/)
  assert.match(brand, /alt="Don’tRipIt"/)
})

test('round DR mark is explicit in nav and auth stays on horizontal wordmark', () => {
  const nav = read('components/layout/TopNav.js')
  const auth = read('components/auth/AuthShell.js')
  const placement = read('app/lovable-v3-brand-placement.css')

  assert.match(nav, /<BrandMark variant="nav" \/>/)
  assert.doesNotMatch(auth, /<BrandMark compact \/>/)
  assert.match(auth, /dri-auth-mobile-brand"><BrandMark \/>/)
  assert.match(placement, /The circular DR! mark is navigation-only/)
  assert.match(placement, /width:\s*42px !important/)
  assert.match(placement, /width:\s*36px !important/)
  assert.match(placement, /overflow:\s*visible !important/)
  assert.match(placement, /\.dri-footer \.dri-brand-official-wordmark/)
})

test('clean interior system is centered, sans, dark and does not target Home composition', () => {
  const css = read('app/lovable-v3-interior-clean.css')
  assert.match(css, /--v3-shell:\s*1200px/)
  assert.match(css, /--v3-reading:\s*680px/)
  assert.match(css, /font-family:var\(--font-body\)/)
  assert.match(css, /grid-template-columns:224px minmax\(0,1fr\)/)
  assert.doesNotMatch(css, /\.v17-/)
  assert.doesNotMatch(css, /\.v5-home/)
})

test('card result price is optional and only reads exact matched-print market', () => {
  const card = read('components/catalog/CatalogCard.js')
  assert.match(card, /function cardCornerMarket/)
  assert.match(card, /item\?\.card_market/)
  assert.match(card, /item\.type === 'card' && cardMarket/)
  assert.match(card, /v14-card-price-corner/)
  assert.match(card, /Precio exacto de la impresión física mostrada/)
  assert.doesNotMatch(card, /v14-card-price-corner[^]*Sin precio actual/)
})

test('search BFF enriches cards from the matched physical print only', () => {
  const route = read('app/api/catalog/search/route.js')
  assert.match(route, /matched_print_id:\s*matched\.print_id \?\? matched\.id \?\? null/)
  assert.match(route, /card_market:\s*matched\.market \|\| null/)
  assert.match(route, /async function enrichCardsWithMatchedPrintMarket/)
  assert.match(route, /item\?\.matched_print_id/)
  assert.match(route, /\/api\/v1\/market\/prints\/summary/)
  assert.match(route, /card_market:\s*market/)
  assert.match(route, /enrichCardsWithMatchedPrintMarket\(selectedRows\)/)
  assert.match(route, /enrichCardsWithMatchedPrintMarket\(pageItems\)/)
})
