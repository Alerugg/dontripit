const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('mobile search cards enlarge images instead of shrinking them', () => {
  const css = source('components/searchV2/SearchV2Polish.css')

  assert.match(css, /@media \(max-width: 700px\)/)
  assert.match(css, /grid-template-columns:\s*clamp\(108px, 30vw, 120px\) minmax\(0,1fr\)/)
  assert.match(css, /\.sv2-result-image-wrap \{ width: clamp\(108px, 30vw, 120px\); \}/)
  assert.doesNotMatch(css, /\.sv2-result-image-wrap \{ width: 74px; \}/)
})

test('mobile exact-market row can flow below the card content', () => {
  const css = source('components/searchV2/SearchV2Polish.css')
  const results = source('components/searchV2/SearchV2Results.js')

  assert.match(results, /className="sv2-market-row"/)
  assert.doesNotMatch(results, /gridColumn:\s*'2'/)
  assert.match(css, /\.sv2-market-row \{[\s\S]*?grid-column:\s*2;/)
  assert.match(css, /@media \(max-width: 700px\)[\s\S]*?\.sv2-market-row \{[\s\S]*?grid-column:\s*1 \/ -1;/)
})
