import test from 'node:test'
import assert from 'node:assert/strict'

const setsNormalizerModule = () => import(`../lib/catalog/normalizers/sets.js?ts=${Date.now()}`)

test('One Piece canonical mapping uses canonical labels only on unequivocal matches', async () => {
  const sets = await setsNormalizerModule()

  const fallback = sets.selectBestSearchFallback(
    { id: 1188, code: '1188', name: '1188' },
    [
      { id: 1188, set_code: 'EB-02', title: 'Extra Booster Memorial Collection' },
    ],
  )

  assert.equal(fallback?.set_code, 'EB-02')

  const normalized = sets.normalizeSet({ id: 1188, code: '1188', name: '1188', game_slug: 'onepiece' }, fallback)
  assert.equal(normalized.code, 'EB-02')
  assert.equal(normalized.title, 'Extra Booster Memorial Collection')
})

test('One Piece canonical mapping does not collapse ambiguous numeric sets into the same fallback code', async () => {
  const sets = await setsNormalizerModule()

  const ambiguousResults = [
    { id: 5001, set_code: 'EB-01', title: 'Memorial Collection' },
    { id: 5002, set_code: 'EB-02', title: 'Anime 25th Collection' },
  ]

  const fallback = sets.selectBestSearchFallback(
    { id: 1189, code: '1189', name: '1189' },
    ambiguousResults,
  )

  assert.equal(fallback, null)

  const normalized = sets.normalizeSet({ id: 1189, code: '1189', name: '1189', game_slug: 'onepiece' }, fallback)
  assert.equal(normalized.code, '1189')
  assert.equal(normalized.title, 'Set #1189')
})

test('One Piece normalization keeps distinct ids from sharing a heuristic canonical code', async () => {
  const sets = await setsNormalizerModule()

  const degradedItems = [
    { id: 1188, code: '1188', name: '1188', game_slug: 'onepiece', card_count: 14 },
    { id: 1189, code: '1189', name: '1189', game_slug: 'onepiece', card_count: 17 },
    { id: 1191, code: '1191', name: '1191', game_slug: 'onepiece', card_count: 9 },
  ]

  const ambiguousResults = [
    { id: 5001, set_code: 'EB-01', title: 'Memorial Collection' },
    { id: 5002, set_code: 'EB-02', title: 'Anime 25th Collection' },
  ]

  const normalized = degradedItems.map((item) => {
    const fallback = sets.selectBestSearchFallback(item, ambiguousResults)
    return sets.normalizeSet(item, fallback)
  })

  const distinctCodes = new Set(normalized.map((item) => item.code))
  assert.equal(distinctCodes.size, degradedItems.length)
  assert.deepEqual(normalized.map((item) => item.code), ['1188', '1189', '1191'])
  assert.deepEqual(normalized.map((item) => item.title), ['Set #1188', 'Set #1189', 'Set #1191'])
  assert.deepEqual(normalized.map((item) => item.card_count), [14, 17, 9])
})

test('normalizeSet preserves required payload fields', async () => {
  const sets = await setsNormalizerModule()

  const normalized = sets.normalizeSet({
    id: 1195,
    code: '1195',
    name: '1195',
    game_slug: 'onepiece',
    card_count: 32,
  })

  assert.deepEqual(Object.keys(normalized).sort(), [
    'card_count',
    'code',
    'game',
    'game_slug',
    'id',
    'name',
    'set_code',
    'title',
  ])
  assert.equal(normalized.id, 1195)
  assert.equal(normalized.code, '1195')
  assert.equal(normalized.set_code, '1195')
  assert.equal(normalized.name, 'Set #1195')
  assert.equal(normalized.title, 'Set #1195')
  assert.equal(normalized.game, 'onepiece')
  assert.equal(normalized.game_slug, 'onepiece')
  assert.equal(normalized.card_count, 32)
})
