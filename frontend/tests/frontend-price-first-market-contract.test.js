const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('Explorer cards show only exact matched prices and stay silent when price is absent', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /mapping_confidence !== 'exact'/)
  assert.match(card, /Precio de impresión exacta/)
  assert.match(card, /if \(!market\) return null/)
  assert.match(card, /Print \$\{market\.printId\}/)
  assert.doesNotMatch(card, /Sin precio actual/)
  assert.doesNotMatch(card, /N\/D|No disponible/)
})

test('Canonical Card detail exposes an honest range without inventing a universal price', () => {
  const detail = source('components/cards/CardDetailLayout.js')
  const browser = source('components/cards/CardVersionBrowser.js')
  assert.match(detail, /marketSummary\.range/)
  assert.match(detail, /impresiones con precio exacto/)
  assert.match(detail, /no es un precio universal de la carta/)
  assert.match(detail, /onMarketSummary=\{setMarketSummary\}/)
  assert.match(browser, /price_guides/)
  assert.match(browser, /VersionMarket/)
  assert.match(browser, /Precio exacto · Cardmarket/)
  assert.match(browser, /guideVariantLabel/)
  assert.doesNotMatch(browser, /Sin precio actual/)
})

test('Exact Print page uses a safe locale and makes the exact market price the headline', () => {
  const printPage = source('app/prints/[id]/page.js')
  assert.match(printPage, /function normalizeLocaleTag/)
  assert.match(printPage, /split\('@'\)/)
  assert.match(printPage, /replace\(\/_\/g, '-'\)/)
  assert.match(printPage, /function formatMarketDate/)
  assert.match(printPage, /const primaryValue = price\.trend/)
  assert.match(printPage, /Precio de mercado/)
  assert.match(printPage, /if \(!price\) return null/)
  assert.doesNotMatch(printPage, /toLocaleDateString\(locale/)
})

test('Price-first visual layer supports result, version, card range and print-detail hierarchy', () => {
  const css = source('app/price-first-market.css')
  for (const token of [
    '.v15-result-price-card',
    '.v15-card-market-range',
    '.v15-version-market',
    '.v15-version-price-line',
    '.v15-print-market-panel',
  ]) assert.match(css, new RegExp(token.replace('.', '\\.')))
  assert.match(css, /@media \(max-width: 620px\)/)
  assert.match(css, /prefers-reduced-motion/)
})
