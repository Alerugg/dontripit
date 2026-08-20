const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('P0.4 canonical cards expose physical coverage without inventing a universal price', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /function CardSignal/)
  assert.match(card, /Cobertura física/)
  assert.match(card, /variant_count/)
  assert.match(card, /el mercado pertenece a cada impresión física/)
  assert.doesNotMatch(card, /cardmarket_price/)
})

test('P0.4 exact Prints make verified current Cardmarket data the primary signal', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /function PrintMarketSignal/)
  assert.match(card, /Cardmarket exacto/)
  assert.match(card, /market\?\.display_price/)
  assert.match(card, /market\?\.price_low/)
  assert.match(card, /market\?\.as_of/)
  assert.match(card, /Sin precio actual/)
  assert.match(card, /No mostramos estimaciones ni precios de otra edición/)
})

test('P0.4 Sets use source fields and never reuse a Print market block', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /function SetSignal/)
  assert.match(card, /Contenido del set/)
  assert.match(card, /item\.card_count \?\? item\.total_cards \?\? item\.cards_count/)
  assert.match(card, /item\.set_code \|\| item\.code/)
  assert.match(card, /item\.type === 'print' \? <PrintMarketSignal/)
  assert.match(card, /item\.type === 'set' \? <SetSignal/)
})

test('P0.4 result-card stylesheet is globally loaded and responsive', () => {
  const layout = source('app/layout.js')
  const css = source('app/lovable-v2-result-cards.css')
  assert.match(layout, /import '\.\/lovable-v2-result-cards\.css'/)
  assert.match(css, /\.v8-result-market/)
  assert.match(css, /\.v8-result-card-signal/)
  assert.match(css, /\.v8-result-set-signal/)
  assert.match(css, /@media \(max-width: 780px\)/)
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/)
})
