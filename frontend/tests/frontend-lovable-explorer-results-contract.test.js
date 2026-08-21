const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('Explorer follows Lovable context-search-tabs-body composition', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const css = source('app/lovable-v2-explorer.css')

  for (const token of [
    'v13-explorer-top',
    'v13-explorer-header',
    'v13-explorer-search',
    'v13-result-tabs',
    'v13-explorer-body',
    'v13-explorer-sidebar',
    'v13-results-toolbar',
  ]) assert.match(explorer, new RegExp(token))

  assert.match(css, /grid-template-columns:\s*16rem minmax\(0, 1fr\)/)
  assert.match(css, /\.v13-explorer-sidebar\.catalog-sidebar[\s\S]*position:\s*sticky/)
})

test('sort and view remain reading controls while physical filters stay in the filter panel', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  assert.match(explorer, /className="v13-results-controls"/)
  assert.match(explorer, /className="v13-sort-control"/)
  assert.match(explorer, /className="segmented v13-view-toggle"/)
  assert.match(explorer, /className="filter-group v13-exact-price-filter"/)
  assert.match(explorer, /Solo impresiones con precio exacto/)
})

test('mobile Explorer exposes filters without hiding sort or view controls', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  const css = source('app/lovable-v2-explorer.css')
  assert.match(explorer, /<details className="v13-mobile-filters">/)
  assert.match(css, /@media \(max-width: 900px\)[\s\S]*\.v13-explorer-sidebar[\s\S]*display:\s*none/)
  assert.match(css, /@media \(max-width: 900px\)[\s\S]*\.v13-mobile-filters[\s\S]*display:\s*block/)
})

test('grid cards keep physical-card proportion and list mode becomes a dense row surface', () => {
  const css = source('app/lovable-v2-result-cards.css')
  assert.match(css, /aspect-ratio:\s*63 \/ 88/)
  assert.match(css, /\.results-list[\s\S]*overflow:\s*hidden/)
  assert.match(css, /grid-template-columns:\s*62px minmax\(0, 1fr\)/)
  assert.match(css, /@media \(max-width: 780px\)[\s\S]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/)
})

test('Card, Print and Set remain visually and semantically distinct without demo values', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /item\.type === 'set' \? \(/)
  assert.match(card, /<SetCover item=\{item\} title=\{title\} \/>/)
  assert.match(card, /item\.type === 'print' \? <PrintMarketSignal market=\{market\} \/>/)
  assert.match(card, /item\.type !== 'print' && item\.type !== 'set' \? <CardSignal item=\{item\} \/>/)
  assert.match(card, /if \(item\?\.type !== 'print'\) return null/)
  assert.doesNotMatch(card, /Math\.random|mockPrice|demoPrice|fakePrice/)
})
