const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('set directory uses the shared canonical game catalog instead of a hardcoded mtg UI slug', () => {
  const page = source('app/games/[slug]/sets/page.js')
  assert.match(page, /import \{ getGameConfig \} from/)
  assert.match(page, /const game = getGameConfig\(requestedSlug\)/)
  assert.doesNotMatch(page, /const GAMES =/)
  assert.doesNotMatch(page, /mtg:\s*\{ slug: 'mtg'/)
})

test('legacy API-style game aliases redirect to the canonical UI set directory', () => {
  const page = source('app/games/[slug]/sets/page.js')
  assert.match(page, /if \(requestedSlug !== game\.slug\) redirect\(`\/games\/\$\{game\.slug\}\/sets`\)/)
  const games = source('lib/catalog/games.js')
  assert.match(games, /mtg: 'magic'/)
})

test('set directory restores global navigation and exposes canonical metadata', () => {
  const page = source('app/games/[slug]/sets/page.js')
  assert.match(page, /<TopNav \/>/)
  assert.match(page, /<GameCollectionsDirectoryPage game=\{game\} \/>/)
  assert.match(page, /generateMetadata/)
  assert.match(page, /const canonical = `\/games\/\$\{game\.slug\}\/sets`/)
  assert.match(page, /alternates: \{ canonical \}/)
})

test('catalog helpers keep Magic UI canonical while translating only at the API boundary', () => {
  const games = source('lib/catalog/games.js')
  const routes = source('lib/catalog/routes.js')
  const client = source('lib/catalog/client.js')
  assert.match(games, /slug: 'magic'/)
  assert.match(games, /magic: 'mtg'/)
  assert.match(routes, /`\/games\/\$\{normalizeGameSlug\(slug\)\}`/)
  assert.match(client, /game: toApiGameSlug\(game \|\| ''\)/)
})
