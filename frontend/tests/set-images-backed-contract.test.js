const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('set image policy does not invent unbacked /sets asset paths', () => {
  const helper = source('lib/catalog/setImages.js')
  const list = source('components/games/GameCollectionsList.js')

  assert.doesNotMatch(helper, /`\/sets\/\$\{/)
  assert.match(helper, /getLocalSetImageCandidates\(\)[\s\S]*?return \[\]/)
  assert.doesNotMatch(list, /buildSetImageSrc/)
  assert.doesNotMatch(list, /<img[\s\S]*?game-collection-logo/)
  assert.match(list, /game-collection-media-fallback/)
})
