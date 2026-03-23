import test from 'node:test'
import assert from 'node:assert/strict'

test('buildMasterCards groups prints only by card_id and preserves backend variant_count', async () => {
  const { buildMasterCards } = await import(`../lib/catalog/normalizers/search.js?ts=${Date.now()}`)

  const items = [
    {
      type: 'print',
      id: 101,
      card_id: 7,
      title: 'Charizard',
      game: 'pokemon',
      set_code: 'BS',
      collector_number: '4',
      variant: 'default',
      variant_count: 3,
      primary_image_url: 'https://img.example/charizard-base.png',
    },
    {
      type: 'print',
      id: 102,
      card_id: 7,
      title: 'Charizard',
      game: 'pokemon',
      set_code: 'BS',
      collector_number: '4',
      variant: 'holo',
      variant_count: 3,
      primary_image_url: 'https://img.example/charizard-holo.png',
    },
    {
      type: 'print',
      id: 103,
      card_id: 8,
      title: 'Charizard',
      game: 'pokemon',
      set_code: 'SVP',
      collector_number: '56',
      variant: 'promo',
      variant_count: 1,
      primary_image_url: 'https://img.example/charizard-promo.png',
    },
  ]

  const masters = buildMasterCards(items)
  assert.equal(masters.length, 2)

  const [baseCard, promoCard] = masters.sort((left, right) => left.card_id - right.card_id)
  assert.equal(baseCard.card_id, 7)
  assert.equal(baseCard.variant_count, 3)
  assert.deepEqual(baseCard.variants.map((variant) => variant.id), [101, 102])

  assert.equal(promoCard.card_id, 8)
  assert.equal(promoCard.variant_count, 1)
  assert.deepEqual(promoCard.variants.map((variant) => variant.id), [103])
})
