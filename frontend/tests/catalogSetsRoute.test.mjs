import test from 'node:test'
import assert from 'node:assert/strict'

const setsNormalizerModule = () => import(`../lib/catalog/normalizers/sets.js?ts=${Date.now()}`)

test('One Piece canonical mapping uses a unique, non-numeric fallback when mapping is unequivocal', async () => {
  const sets = await setsNormalizerModule()

  const fallback = sets.selectBestSearchFallback(
    { id: 1188, code: '1188', name: '1188' },
    [
      { id: 9001, set_code: 'EB-02', title: 'Extra Booster Memorial Collection' },
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
    { id: 1188, code: '1188', name: '1188', game_slug: 'onepiece' },
    { id: 1189, code: '1189', name: '1189', game_slug: 'onepiece' },
    { id: 1191, code: '1191', name: '1191', game_slug: 'onepiece' },
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
})
