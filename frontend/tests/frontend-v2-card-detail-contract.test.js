const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('P0.5 Card detail presents logical identity and truthful physical coverage', () => {
  const detail = source('components/cards/CardDetailLayout.js')
  assert.match(detail, /v9-identity-pill">Carta canónica/)
  assert.match(detail, /prints_pagination\?\.total/)
  assert.match(detail, /Sets relacionados/)
  assert.match(detail, /Solo en la impresión exacta/)
  assert.doesNotMatch(detail, /display_price|price_market|cardmarket_price/)
})

test('P0.5 version browser makes exact Print links the primary conversion path', () => {
  const browser = source('components/cards/CardVersionBrowser.js')
  assert.match(browser, /Elige la edición exacta/)
  assert.match(browser, /Cada enlace de idioma termina en una Print concreta/)
  assert.match(browser, /getPrintHref\(exactPrint\.print_id\)/)
  assert.match(browser, /getPrintHref\(print\.print_id\)/)
  assert.match(browser, /getPrintHref\(singleExactPrint\.print_id\)/)
  assert.match(browser, /Abrir impresión exacta/)
})

test('P0.5 Cardmarket group identity is labelled as reference rather than exact valuation', () => {
  const browser = source('components/cards/CardVersionBrowser.js')
  assert.match(browser, /Identidad Cardmarket enlazada/)
  assert.match(browser, /Sin enlace Cardmarket exacto/)
  assert.match(browser, /Referencia de producto Cardmarket/)
  assert.doesNotMatch(browser, /price_guides|display_price|price_market|Cardmarket pendiente/)
})

test('P0.5 Card detail styling is global, responsive and reduced-motion safe', () => {
  const layout = source('app/layout.js')
  const css = source('app/lovable-v2-card-detail.css')
  const browserCss = source('components/cards/CardVersionBrowser.module.css')
  assert.match(layout, /import '\.\/lovable-v2-card-detail\.css'/)
  assert.match(css, /\.v9-card-truth/)
  assert.match(css, /\.v9-related-set-grid/)
  assert.match(css, /@media \(max-width: 720px\)/)
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/)
  assert.match(browserCss, /\.identityLinked/)
  assert.match(browserCss, /\.primaryAction/)
  assert.match(browserCss, /@media\(max-width:620px\)/)
})
