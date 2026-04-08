import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('catalog card route enforces requested card identity and filters prints by card_id', async () => {
  const source = await fs.readFile(new URL('../app/api/catalog/cards/[id]/route.js', import.meta.url), 'utf8')

  assert.match(source, /error: 'catalog_card_not_found'/)
  assert.match(source, /payloadCardIdAsNumber !== requestedCardIdAsNumber/)
  assert.match(source, /payload\.prints\.filter\(\(print\) =>/)
  assert.match(source, /printCardIdAsNumber === requestedCardIdAsNumber/)
  assert.match(source, /\.sort\(comparePrints\)/)
})

test('catalog card links prefer master card id for card entities', async () => {
  const source = await fs.readFile(new URL('../components/catalog/CatalogCard.js', import.meta.url), 'utf8')

  assert.match(source, /const resolvedCardId = item\.type === 'card' \? item\.id : \(item\.card_id \|\| null\)/)
})

test('card detail layout uses route slug fallback and canonical game label for One Piece links', async () => {
  const source = await fs.readFile(new URL('../components/cards/CardDetailLayout.js', import.meta.url), 'utf8')

  assert.match(source, /routeGameSlug = ''/)
  assert.match(source, /normalizeGameSlug\(routeGameSlug \|\| card\?\.game_slug \|\| card\?\.game \|\| ''\)/)
  assert.match(source, /const gameLabel = gameConfig\?\.name \|\| card\?\.game \|\| 'TCG'/)
})
