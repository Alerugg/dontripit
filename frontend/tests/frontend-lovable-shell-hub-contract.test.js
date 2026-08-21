const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('global shell uses the restrained Lovable V2 chrome', () => {
  const layout = source('app/layout.js')
  const css = source('app/lovable-v2-shell.css')
  const nav = source('components/layout/TopNav.js')

  assert.match(layout, /import '\.\/lovable-v2-shell\.css'/)
  assert.match(css, /\.dri-nav[\s\S]*position:\s*sticky/)
  assert.match(css, /grid-template-columns:\s*auto minmax\(0, 1fr\) auto/)
  assert.match(nav, /<Menu label="Juegos">/)
  assert.match(nav, /<Menu label="Mi cartera">/)
  assert.match(nav, /aria-controls="dri-mobile-nav"/)
  assert.match(nav, /href="\/explorer"/)
})

test('game hub remains a catalog workspace rather than a promotional page', () => {
  const hub = source('components/games/GameHubPage.js')
  const css = source('components/games/GameHubV2.css')

  assert.match(hub, /Carta → impresión → mercado/)
  assert.match(hub, /<CatalogExplorer/)
  assert.match(hub, /allowGameSelect=\{false\}/)
  assert.match(hub, /Fechas que sí están verificadas/)
  assert.match(hub, /No usamos fechas antiguas ni de otra región/)
  assert.match(css, /\.v6-game-hero-main[\s\S]*grid-template-columns:/)
  assert.match(css, /\.v6-game-secondary[\s\S]*border-top:/)
  assert.match(css, /@media \(max-width:\s*720px\)/)
})

test('shell and hub include keyboard focus and reduced-motion handling', () => {
  const shell = source('app/lovable-v2-shell.css')
  const hubCss = source('components/games/GameHubV2.css')

  assert.match(shell, /:focus-visible/)
  assert.match(shell, /@media \(prefers-reduced-motion:\s*reduce\)/)
  assert.match(hubCss, /:focus-visible/)
  assert.match(hubCss, /@media \(prefers-reduced-motion:\s*reduce\)/)
})
