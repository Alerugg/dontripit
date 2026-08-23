const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('Print detail makes exact physical identity explicit above secondary card content', () => {
  const page = source('app/prints/[id]/page.js')
  assert.match(page, /Impresión exacta/)
  assert.match(page, /Print ID \{printDetail\.id\}/)
  assert.match(page, /Identidad física exacta/)
  assert.match(page, /Idioma, acabado, variante y precio pertenecen a esta Print física concreta/)
  assert.match(page, /<IdentityChip accent>\{versionCode\}<\/IdentityChip>/)
  assert.match(page, /printDetail\.language\?\.toUpperCase\(\)/)
  assert.match(page, /finishLabel/)
})

test('ownership and exact market appear before card text', () => {
  const page = source('app/prints/[id]/page.js')
  const ownership = page.indexOf('<LibraryActions printId={printDetail.id} />')
  const market = page.indexOf('<PriceBlock price={price} cardmarket={cardmarket} locale={DEFAULT_DISPLAY_LOCALE} />')
  const cardText = page.indexOf('className="panel-soft identifiers v14-card-text"')
  assert.ok(ownership >= 0, 'missing exact Print library actions')
  assert.ok(market > ownership, 'market should follow exact ownership actions')
  assert.ok(cardText > market, 'card text must remain secondary to exact identity and market')
})

test('market panel stays fail-closed and keeps verified Cardmarket concepts', () => {
  const page = source('app/prints/[id]/page.js')
  for (const label of ['Mínimo', 'Conservador', 'Tendencia', 'Media']) {
    assert.ok(page.includes(label), `missing price label ${label}`)
  }
  assert.match(page, /if \(!price\) return null/)
  assert.match(page, /if \(!primary\) return null/)
  assert.match(page, /positiveNumber\(price\.trend\)/)
  assert.match(page, /No reutilizamos el precio de otra edición/)
  assert.match(page, /Low Price EX\+/)
  assert.match(page, /Foil Low/)
  assert.match(page, /Ver esta Print en Cardmarket/)
  assert.doesNotMatch(page, /Sin enlace Cardmarket exacto disponible/)
  assert.doesNotMatch(page, /value \|\| 0/)
})

test('Cardmarket CTA lives in the market panel and is not duplicated in related navigation', () => {
  const page = source('app/prints/[id]/page.js')
  const navigationStart = page.indexOf('className="dri-exact-navigation v14-exact-navigation"')
  assert.ok(navigationStart >= 0)
  const navigation = page.slice(navigationStart)
  assert.doesNotMatch(navigation, /Cardmarket/)
  assert.match(navigation, /Todas las impresiones/)
  assert.match(navigation, /Ver edición física/)
})

test('Print detail V2 is responsive and reduced-motion safe', () => {
  const css = source('app/prints/[id]/PrintDetailV2.css')
  assert.match(css, /\.v14-print-page/)
  assert.match(css, /\.v14-market-panel/)
  assert.match(css, /\.v14-identity-chips/)
  assert.match(css, /@media \(max-width: 760px\)/)
  assert.match(css, /@media \(max-width: 460px\)/)
  assert.match(css, /prefers-reduced-motion/)
})
