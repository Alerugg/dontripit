import test from 'node:test'
import assert from 'node:assert/strict'
import { appendAdvancedFilters, hasFilterValues, readAdvancedFilters, safePage } from '../lib/searchV2/urlState.js'

test('advanced filter URL state preserves scalar 1, booleans, arrays and numeric ranges', () => {
  const filters = {
    block: '1',
    promo: true,
    traits: ['Straw Hat Crew', 'Supernovas'],
    power: { min: 5000, max: 10000 },
    set: 'op-05',
  }
  const params = new URLSearchParams()
  appendAdvancedFilters(params, filters)

  assert.equal(params.get('f_block'), '1')
  assert.notEqual(params.get('f_promo'), '1')
  assert.deepEqual(params.getAll('f_traits'), ['Straw Hat Crew', 'Supernovas'])

  assert.deepEqual(readAdvancedFilters(params), filters)
})

test('advanced URL helpers reject invalid pages and detect meaningful filters', () => {
  assert.equal(safePage('3'), 3)
  assert.equal(safePage('0'), 1)
  assert.equal(safePage('abc'), 1)
  assert.equal(hasFilterValues({}), false)
  assert.equal(hasFilterValues({ power: { min: undefined, max: undefined } }), false)
  assert.equal(hasFilterValues({ block: '1' }), true)
})
