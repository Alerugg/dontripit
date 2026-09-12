import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CATALOG_CACHE_HEADERS,
  cleanOptionalMetadata,
  firstCleanMetadata,
} from '../lib/catalog/metadata.js'

test('catalog cache policy never serves stale responses', () => {
  assert.equal(
    CATALOG_CACHE_HEADERS['Cache-Control'],
    'public, s-maxage=15, must-revalidate',
  )
  assert.equal(
    CATALOG_CACHE_HEADERS['Cache-Control'].includes('stale-while-revalidate'),
    false,
  )
})

test('optional metadata placeholders become null', () => {
  for (const value of ['', ' ', '-', '?', 'N/A', 'na', 'None', 'null', 'UNKNOWN', 'undefined']) {
    assert.equal(cleanOptionalMetadata(value), null)
  }
  assert.equal(cleanOptionalMetadata(null), null)
  assert.equal(cleanOptionalMetadata(undefined), null)
})

test('valid optional metadata is preserved and trimmed', () => {
  assert.equal(cleanOptionalMetadata(' Common '), 'Common')
  assert.equal(cleanOptionalMetadata('bs'), 'bs')
  assert.equal(cleanOptionalMetadata(58), 58)
})

test('firstCleanMetadata falls through bad matched metadata to valid canonical metadata', () => {
  assert.equal(firstCleanMetadata('unknown', 'Common'), 'Common')
  assert.equal(firstCleanMetadata(null, 'bs'), 'bs')
  assert.equal(firstCleanMetadata('unknown', 'null', null), null)
})
