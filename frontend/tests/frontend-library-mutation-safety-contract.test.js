const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('collection BFF preserves owned metadata during partial quantity writes', () => {
  const route = source('app/api/library/collection/route.js')
  assert.match(route, /preserveExistingCollectionFields/)
  assert.match(route, /callUserApi\('\/api\/v2\/me\/collection'\)/)
  assert.match(route, /quantity: hasOwn\(body, 'quantity'\) \? body\.quantity : existing\.quantity/)
  assert.match(route, /condition: hasOwn\(body, 'condition'\) \? body\.condition : existing\.condition/)
  assert.match(route, /notes: hasOwn\(body, 'notes'\) \? body\.notes : existing\.notes/)
  assert.match(route, /purchase_price: hasOwn\(body, 'purchase_price'\) \? body\.purchase_price : existing\.purchase_price/)
  assert.match(route, /purchase_currency: hasOwn\(body, 'purchase_currency'\) \? body\.purchase_currency : existing\.purchase_currency/)
  assert.match(route, /acquired_at: hasOwn\(body, 'acquired_at'\) \? body\.acquired_at : existing\.acquired_at/)
})

test('wishlist BFF preserves priority and target when a caller sends a partial write', () => {
  const route = source('app/api/library/wishlist/route.js')
  assert.match(route, /preserveExistingWishlistFields/)
  assert.match(route, /callUserApi\('\/api\/v2\/me\/wishlist'\)/)
  assert.match(route, /priority: hasOwn\(body, 'priority'\) \? body\.priority : existing\.priority/)
  assert.match(route, /target_price: hasOwn\(body, 'target_price'\) \? body\.target_price : existing\.target_price/)
  assert.match(route, /target_currency: hasOwn\(body, 'target_currency'\) \? body\.target_currency : existing\.target_currency/)
})

test('exact Print quick actions detect existing membership before writing', () => {
  const actions = source('components/library/LibraryActions.js')
  assert.match(actions, /readMembership\('collection', printId\)/)
  assert.match(actions, /readMembership\('wishlist', printId\)/)
  assert.match(actions, /if \(kind === 'collection' && collectionEntry\)/)
  assert.match(actions, /if \(kind === 'wishlist' && wishlistEntry\)/)
  assert.match(actions, /En colección · \{Number\(collectionEntry\.quantity \|\| 1\)\} ✓/)
  assert.match(actions, /En wishlist ✓/)
  assert.match(actions, /Conservamos su prioridad y precio objetivo/)
})

test('anonymous users still authenticate only when they try to save', () => {
  const actions = source('components/library/LibraryActions.js')
  assert.match(actions, /if \(!response\.ok\) return null/)
  assert.match(actions, /if \(response\.status === 401\)/)
  assert.match(actions, /window\.location\.assign\(`\/login\?next=/)
})
