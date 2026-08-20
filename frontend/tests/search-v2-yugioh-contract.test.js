const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('Yu-Gi-Oh game route keeps one primary Explorer and retains Search V2 in the advanced tool', () => {
  const page = source('app/games/[slug]/page.js')
  const hub = source('components/games/GameHubPage.js')
  const advanced = source('app/games/[slug]/advanced/page.js')

  assert.match(page, /GameHubPage/)
  assert.match(page, /game\.slug === 'riftbound'/)
  assert.match(page, /<GameHubPage game=\{game\}/)
  assert.match(hub, /<CatalogExplorer/)
  assert.match(hub, /yugioh:/)
  assert.match(hub, /\/games\/\$\{game\.slug\}\/advanced\?advanced=1/)
  assert.doesNotMatch(hub, /<OnePieceSearchV2Experience/)
  assert.match(advanced, /OnePieceSearchV2Experience/)
  assert.match(advanced, /<OnePieceSearchV2Experience game=\{game\}/)
  assert.match(advanced, /next\.set\('advanced', '1'\)/)
  assert.doesNotMatch(hub, /dri-hub-card-stack/)
})

test('Yu-Gi-Oh V2 explorer retains certified counts as non-user-facing evidence', () => {
  const explorer = source('components/games/YugiohExplorerV2Page.js')
  assert.match(explorer, /14\.479 Cards/)
  assert.match(explorer, /44\.226 Prints/)
  assert.match(explorer, /20 facets · 19 activos/)
})

test('shared Search V2 experience exposes localized Yu-Gi-Oh-native examples', () => {
  const experience = source('components/searchV2/OnePieceSearchV2Experience.js')
  assert.match(experience, /yugioh:/)
  assert.match(experience, /Dark Magician/)
  assert.match(experience, /Mago Oscuro/)
  assert.match(experience, /ブラック・マジシャン/)
})

test('Yu-Gi-Oh exposes prominent all EN ES JA multi-select language controls', () => {
  const experience = source('components/searchV2/OnePieceSearchV2Experience.js')

  assert.match(experience, /YUGIOH_LANGUAGE_CODES = \['en', 'es', 'ja'\]/)
  assert.match(experience, /Todos/)
  assert.match(experience, /English/)
  assert.match(experience, /Español/)
  assert.match(experience, /日本語/)
  assert.match(experience, /searchLanguages\.join\(','\)/)
  assert.match(experience, /aria-label="Filtrar Yu-Gi-Oh por idioma"/)
  assert.match(experience, /aria-pressed=\{searchLanguages\.includes\(language\)\}/)
})

test('Yu-Gi-Oh localized language remains separate from physical print identity', () => {
  const results = source('components/searchV2/SearchV2Results.js')
  const backendNormal = source('../backend/app/search_v2/yugioh_query.py')
  const backendAdvanced = source('../backend/app/search_v2/yugioh_advanced.py')

  assert.match(results, /item\.display_language \|\| matched\.display_language \|\| matched\.language/)
  assert.match(results, /item\.display_language \|\| item\.language/)
  assert.match(backendNormal, /print_localizations/)
  assert.match(backendNormal, /string_to_array\(:display_language, ','\)/)
  assert.match(backendNormal, /"print_id": row\["print_id"\]/)
  assert.match(backendAdvanced, /print_localizations/)
  assert.match(backendAdvanced, /string_to_array\(:display_language, ','\)/)
  assert.match(backendAdvanced, /"print_id": row\["print_id"\]/)
})

test('Yu-Gi-Oh quick filters match the certified backend contract', () => {
  const panel = source('components/searchV2/AdvancedSearchPanel.js')
  for (const key of ['set', 'release', 'card_class', 'attribute', 'archetype', 'rarity']) {
    assert.ok(panel.includes(`'${key}'`), `missing Yu-Gi-Oh quick filter ${key}`)
  }
  assert.match(panel, /yugioh: new Set/)
})

test('Yu-Gi-Oh result cards surface source-backed evidence only', () => {
  const results = source('components/searchV2/SearchV2Results.js')
  assert.match(results, /gameSlug === 'yugioh'/)
  assert.match(results, /physical\.card_class/)
  assert.match(results, /physical\.attribute/)
  assert.match(results, /physical\.race/)
  assert.match(results, /ATK/)
  assert.match(results, /DEF/)
})

test('backend Search V2 dispatcher explicitly routes Yu-Gi-Oh alongside other certified games', () => {
  const routes = source('../backend/app/routes/search_v2.py')
  assert.match(routes, /normal_yugioh_search/)
  assert.match(routes, /advanced_yugioh_search/)
  assert.match(routes, /yugioh_facet_values/)
  assert.match(routes, /SEARCH_V2_ADVANCED_GAMES\s*=\s*\{[^}]*"onepiece"[^}]*"pokemon"[^}]*"yugioh"[^}]*"mtg"[^}]*\}/)
  assert.match(routes, /comma-separated selection of: en, es, ja/)
})
