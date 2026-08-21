const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('global explorer is a real catalog workspace instead of a home redirect', () => {
  const page = source('app/explorer/page.js')
  assert.match(page, /CatalogExplorer/)
  assert.match(page, /initialQuery=\{query\}/)
  assert.match(page, /initialType=\{kind\}/)
  assert.doesNotMatch(page, /redirect\('\/'\)/)
})

test('home search supports global or game-scoped canonical-card results while exact suggestions keep their routes', () => {
  const homeSearch = source('components/home/HomeSearch.js')
  assert.match(homeSearch, /suggestCatalog/)
  assert.match(homeSearch, /ACTIVE_GAME_CATALOG/)
  assert.match(homeSearch, /scopedSearchHref/)
  assert.match(homeSearch, /`\/games\/\$\{game\}\$\{suffix\}`/)
  assert.match(homeSearch, /`\/explorer\$\{suffix\}`/)
  assert.match(homeSearch, /getCardHref/)
  assert.match(homeSearch, /getPrintHref/)
  assert.match(homeSearch, /getSetHref/)
})

test('autocomplete reserves plain Enter for all results and arrows for exact suggestions', () => {
  const input = source('components/search/SearchInput.js')
  assert.match(input, /useState\(-1\)/)
  assert.match(input, /activeIndex >= 0 && suggestions\[activeIndex\]/)
  assert.match(input, /Ver todos los resultados para/)
  assert.match(input, /runFullSearch\(\)/)
})

test('explorer exposes counted canonical cards, exact prints, sets and numbered pagination', () => {
  const explorer = source('components/catalog/CatalogExplorer.js')
  assert.match(explorer, /\{ value: 'card', label: 'Cartas', countKey: 'card' \}/)
  assert.match(explorer, /\{ value: 'print', label: 'Impresiones', countKey: 'print' \}/)
  assert.match(explorer, /\{ value: 'set', label: 'Sets', countKey: 'set' \}/)
  assert.match(explorer, /countKey: 'all'/)
  assert.match(explorer, /formatCount\(counts\[option\.countKey\]\)/)
  assert.match(explorer, /fetchCatalogCounts/)
  assert.match(explorer, /if \(!result\.counts_complete\)/)
  assert.match(explorer, /pageWindow/)
  assert.match(explorer, /aria-current=\{safePage === value \? 'page'/)
  assert.match(explorer, /searchCatalog\(\{/)
})

test('catalog result cards reserve market amounts for exact physical prints', () => {
  const card = source('components/catalog/CatalogCard.js')
  assert.match(card, /if \(item\?\.type !== 'print'\) return null/)
  assert.match(card, /const raw = item\?\.market\?\.display_price/)
  assert.doesNotMatch(card, /cardmarket_price/)
  assert.match(card, /Cardmarket exacto/)
  assert.match(card, /No mostramos estimaciones ni precios de otra edición/)
})

test('chosen Editorial Gallery Home and final design override are active', () => {
  const home = source('components/home/PublicHome.js')
  const layout = source('app/layout.js')
  const css = source('app/lovable-v4-home-editorial.css')
  assert.match(home, /className="v17-cover"/)
  assert.match(home, /La misma carta\./)
  assert.match(home, /<em>Objetos<\/em> distintos\./)
  assert.match(home, /<HomeSearch \/>/)
  assert.match(home, /Si no hay correspondencia segura/)
  assert.doesNotMatch(home, /TCG Data\.|<em>Pricing\.<\/em>|Liquidity\./)
  assert.match(layout, /import '\.\/lovable-v2\.css'/)
  assert.match(layout, /import '\.\/lovable-v4-home-editorial\.css'/)
  assert.match(css, /\.v17-cover/)
  assert.match(css, /\.v17-game-gallery/)
})