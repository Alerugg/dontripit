const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('Pokémon game route keeps one primary Explorer and retains Search V2 in the advanced tool', () => {
  const page = source('app/games/[slug]/page.js')
  const hub = source('components/games/GameHubPage.js')
  const advanced = source('app/games/[slug]/advanced/page.js')

  assert.match(page, /GameHubPage/)
  assert.match(page, /game\.slug === 'riftbound'/)
  assert.match(page, /<GameHubPage game=\{game\}/)
  assert.match(hub, /<CatalogExplorer/)
  assert.match(hub, /pokemon:/)
  assert.match(hub, /\/games\/\$\{game\.slug\}\/advanced\?advanced=1/)
  assert.doesNotMatch(hub, /<OnePieceSearchV2Experience/)
  assert.match(advanced, /OnePieceSearchV2Experience/)
  assert.match(advanced, /<OnePieceSearchV2Experience game=\{game\}/)
  assert.match(advanced, /next\.set\('advanced', '1'\)/)
  assert.doesNotMatch(hub, /dri-hub-card-stack/)
})

test('shared Search V2 experience exposes concise Pokémon-native examples', () => {
  const experience = source('components/searchV2/OnePieceSearchV2Experience.js')
  assert.match(experience, /pokemon:/)
  assert.match(experience, /Pikachu/)
  assert.match(experience, /Charizard/)
  assert.match(experience, /151/)
})

test('Pokémon quick filters are game-specific and source-backed', () => {
  const panel = source('components/searchV2/AdvancedSearchPanel.js')
  for (const key of ['types', 'stage', 'rarity', 'regulation_mark', 'finish', 'stamp']) {
    assert.ok(panel.includes(`'${key}'`), `missing Pokémon quick filter ${key}`)
  }
  assert.match(panel, /QUICK_FILTER_KEYS_BY_GAME/)
  assert.match(panel, /Afinar búsqueda/)
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
