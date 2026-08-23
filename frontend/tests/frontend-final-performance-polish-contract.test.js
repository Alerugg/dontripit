const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.join(__dirname, '..')
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8')

const home = read('components/home/PublicHome.js')
const footer = read('components/layout/SiteFooter.js')
const cardDetailCss = read('components/cards/CardDetailV2.css')
const versionsCss = read('components/cards/CardVersionBrowser.module.css')

test('home prioritizes one hero print and defers secondary decorative media', () => {
  assert.match(home, /loading=\{index === 0 \? 'eager' : 'lazy'\}/)
  assert.match(home, /fetchPriority=\{index === 0 \? 'high' : 'low'\}/)
  assert.match(home, /className=\{`v17-game-logo is-\$\{game\.slug\}`\}[\s\S]*loading="lazy"/)
})

test('low-intent home and footer links do not eagerly prefetch routes', () => {
  assert.match(home, /href=\{`\/games\/\$\{game\.slug\}`\}[\s\S]*prefetch=\{false\}[\s\S]*className=\{`v17-game-panel/)
  assert.match(home, /<Link href="\/collection" prefetch=\{false\}>/)
  assert.match(home, /<Link href="\/wishlist" prefetch=\{false\}>/)
  assert.match(footer, /prefetch=\{false\}/)
})

test('card detail reserves stable async market and version-browser space', () => {
  assert.match(cardDetailCss, /\.v9-card-market-rule\s*\{[\s\S]*min-height:\s*92px/)
  assert.match(versionsCss, /\.state\s*\{\s*min-height:420px;display:grid;place-items:center;/)
  assert.match(versionsCss, /\.stateError\s*\{\s*min-height:0;/)
  assert.match(versionsCss, /@media\(max-width:620px\)[\s\S]*\.state\{min-height:320px\}/)
})
