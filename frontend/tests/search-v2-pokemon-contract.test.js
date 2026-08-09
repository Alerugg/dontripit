const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('Pokémon game route uses the shared redesigned hub without losing Search V2', () => {
  const page = source('app/games/[slug]/page.js')
  const hub = source('components/games/GameHubPage.js')

  assert.match(page, /GameHubPage/)
  assert.match(page, /game\.slug === 'riftbound'/)
  assert.match(page, /<GameHubPage game=\{game\}/)
  assert.match(hub, /OnePieceSearchV2Experience/)
  assert.match(hub, /<OnePieceSearchV2Experience game=\{game\}/)
  assert.match(hub, /pokemon:/)
  assert.match(hub, /Pikachu/)
})

test('shared Search V2 experience exposes Pokémon-native examples', () => {
  const experience = source('components/searchV2/OnePieceSearchV2Experience.js')
  assert.match(experience, /pokemon:/)
  assert.match(experience, /Pikachu/)
  assert.match(experience, /Charizard/)
  assert.match(experience, /Special Illustration Rare/)
})

test('Pokémon quick filters are game-specific and source-backed', () => {
  const panel = source('components/searchV2/AdvancedSearchPanel.js')
  for (const key of ['types', 'stage', 'rarity', 'regulation_mark', 'finish', 'stamp']) {
    assert.ok(panel.includes(`'${key}'`), `missing Pokémon quick filter ${key}`)
  }
  assert.match(panel, /QUICK_FILTER_KEYS_BY_GAME/)
})

test('exact Pokémon print cards surface physical identity badges', () => {
  const results = source('components/searchV2/SearchV2Results.js')
  assert.match(results, /physical\.finish/)
  assert.match(results, /physical\.regulation_mark/)
  assert.match(results, /physical\.foil_pattern/)
  assert.match(results, /physical\.stamps/)
})

test('Search V2 client remains game-parameterized', () => {
  const client = source('lib/searchV2/client.js')
  assert.match(client, /searchV2\(\{ q, game/)
  assert.match(client, /fetchFacetsV2\(game\)/)
  assert.match(client, /advancedSearchV2\(\{ game/)
})