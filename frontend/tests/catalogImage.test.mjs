import test from 'node:test'
import assert from 'node:assert/strict'

const imageModule = () => import(`../lib/catalog/image.js?ts=${Date.now()}`)

test('buildMasterCards preserves primary_image_url intact for One Piece and Pokémon items', async () => {
  const { buildMasterCards } = await import(`../lib/catalog/normalizers/search.js?ts=${Date.now()}`)

  const items = [
    {
      type: 'print',
      id: 1,
      card_id: 101,
      title: 'Monkey.D.Luffy',
      game: 'onepiece',
      primary_image_url: 'https://en.onepiece-cardgame.com/images/cardlist/card/EB04-001.png',
    },
    {
      type: 'print',
      id: 2,
      card_id: 202,
      title: 'Sprigatito',
      game: 'pokemon',
      primary_image_url: 'https://assets.tcgdex.net/en/sv/sv1/1/high.webp',
    },
  ]

  const masters = buildMasterCards(items).sort((left, right) => left.card_id - right.card_id)
  assert.equal(masters[0].primary_image_url, 'https://en.onepiece-cardgame.com/images/cardlist/card/EB04-001.png')
  assert.equal(masters[1].primary_image_url, 'https://assets.tcgdex.net/en/sv/sv1/1/high.webp')
})

test('normalizeCatalogImageSrc proxies One Piece official images through the frontend BFF', async () => {
  const { normalizeCatalogImageSrc } = await imageModule()

  const normalized = normalizeCatalogImageSrc('https://en.onepiece-cardgame.com/images/cardlist/card/EB04-001.png')
  assert.equal(
    normalized,
    '/api/catalog/image?src=https%3A%2F%2Fen.onepiece-cardgame.com%2Fimages%2Fcardlist%2Fcard%2FEB04-001.png',
  )
})

test('normalizeCatalogImageSrc repairs outdated TCGdex Scarlet & Violet asset paths before render', async () => {
  const { normalizeCatalogImageSrc } = await imageModule()

  assert.equal(
    normalizeCatalogImageSrc('https://assets.tcgdex.net/en/sv/sv1/1/high.webp'),
    'https://assets.tcgdex.net/en/sv/sv01/001/high.webp',
  )
  assert.equal(
    normalizeCatalogImageSrc('https://assets.tcgdex.net/en/swsh/swsh5/1/high.webp'),
    'https://assets.tcgdex.net/en/swsh/swsh5/1/high.webp',
  )
})
