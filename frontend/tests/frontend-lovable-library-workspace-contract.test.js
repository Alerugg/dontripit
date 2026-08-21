const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('Collection and Wishlist opt into the shared portfolio workspace layer', () => {
  const collection = source('app/collection/page.js')
  const wishlist = source('app/wishlist/page.js')
  assert.match(collection, /LibraryPortfolioV2\.css/)
  assert.match(wishlist, /LibraryPortfolioV2\.css/)
  assert.match(collection, /<LibraryPage kind="collection" \/>/)
  assert.match(wishlist, /<LibraryPage kind="wishlist" \/>/)
})

test('portfolio workspace is dense on desktop instead of a large gallery', () => {
  const css = source('components/library/LibraryPortfolioV2.css')
  assert.match(css, /\.v10-library-grid,[\s\S]*\.v11-wishlist-grid[\s\S]*grid-template-columns:\s*1fr/)
  assert.match(css, /\.v10-library-card,[\s\S]*\.v11-wishlist-card[\s\S]*grid-template-columns:\s*104px minmax\(0, 1fr\)/)
  assert.match(css, /\.v10-library-body[\s\S]*grid-template-columns:\s*minmax\(220px, 1\.3fr\) minmax\(245px, \.9fr\) minmax\(130px, auto\)/)
  assert.match(css, /\.v10-library-toolbar[\s\S]*position:\s*sticky/)
})

test('mobile portfolio rows remain compact exact-Print surfaces', () => {
  const css = source('components/library/LibraryPortfolioV2.css')
  assert.match(css, /@media \(max-width: 760px\)[\s\S]*\.v10-library-card,[\s\S]*grid-template-columns:\s*104px minmax\(0, 1fr\)/)
  assert.match(css, /@media \(max-width: 520px\)[\s\S]*grid-template-columns:\s*88px minmax\(0, 1fr\)/)
  assert.match(css, /prefers-reduced-motion/)
})

test('library product logic keeps exact-Print valuation and no implicit FX', () => {
  const library = source('components/library/LibraryPage.js')
  assert.match(library, /valuation_value \?\? item\?\.latest_price\?\.conservative/)
  assert.match(library, /currentCurrency === targetCurrency/)
  assert.match(library, /No existe un precio Cardmarket actual para esta Print exacta/)
  assert.match(library, /no aplicamos FX implícito/)
  assert.doesNotMatch(library, /Math\.random|mockPrice|demoPrice|fakePrice/)
})
