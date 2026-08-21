const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('Set detail keeps canonical Card and physical Print as separate catalog modes', () => {
  const page = source('components/games/GameSetPage.js')
  assert.match(page, /const \[kind, setKind\] = useState\('card'\)/)
  assert.match(page, /Cartas canónicas/)
  assert.match(page, /Impresiones físicas/)
  assert.match(page, /Idioma de impresión/)
  assert.match(page, /Solo con precio exacto/)
  assert.match(page, /Carta → Impresión → Mercado/)
  assert.match(page, /Una carta canónica nunca recibe un precio universal/)
  assert.match(page, /GameSetPageV2\.css/)
})

test('Set V2 keeps a dense desktop filter workspace and responsive two-column card grid', () => {
  const css = source('components/games/GameSetPageV2.css')
  assert.match(css, /grid-template-columns:\s*16rem minmax\(0, 1fr\)/)
  assert.match(css, /\.dri-set-sidebar[\s\S]*top:\s*70px/)
  assert.match(css, /\.dri-set-results-toolbar[\s\S]*position:\s*sticky/)
  assert.match(css, /@media \(max-width: 680px\)[\s\S]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/)
  assert.match(css, /prefers-reduced-motion/)
})

test('canonical Card detail explicitly refuses a universal market price', () => {
  const page = source('components/cards/CardDetailLayout.js')
  assert.match(page, /Carta canónica/)
  assert.match(page, /impresión física exacta/)
  assert.match(page, /Solo en la impresión exacta/)
  assert.match(page, /No agregamos precios de distintas ediciones, idiomas o acabados/)
  assert.match(page, /CardVersionBrowser/)
  assert.match(page, /CardDetailV2\.css/)
  assert.doesNotMatch(page, /Math\.random|mockPrice|demoPrice|fakePrice/)
})

test('Print detail remains the exact physical identity and market layer', () => {
  const page = source('app/prints/[id]/page.js')
  assert.match(page, /Mercado exacto/)
  assert.match(page, /Cardmarket/)
  assert.match(page, /No reutilizamos el precio de otra edición/)
  assert.match(page, /Impresión exacta/)
  assert.match(page, /Idioma, acabado, variante y precio pertenecen a esta Print física concreta/)
  assert.match(page, /LibraryActions printId=\{printDetail\.id\}/)
  assert.doesNotMatch(page, /Math\.random|mockPrice|demoPrice|fakePrice/)
})

test('Print V2 supports sticky media, responsive market metrics and reduced motion', () => {
  const css = source('app/prints/[id]/PrintDetailV2.css')
  assert.match(css, /\.v14-media-column[\s\S]*position:\s*sticky/)
  assert.match(css, /\.v14-price-grid[\s\S]*repeat\(4,minmax\(0,1fr\)\)/)
  assert.match(css, /@media \(max-width: 760px\)[\s\S]*repeat\(2,minmax\(0,1fr\)\)/)
  assert.match(css, /prefers-reduced-motion/)
})
