const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')

function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('all launch legal pages remain public for signed-out visitors', () => {
  const proxy = source('proxy.js')

  for (const route of ['/privacy', '/cookies', '/terms']) {
    assert.match(proxy, new RegExp(`['\"]${route.replace('/', '\\/')}['\"]`))
  }
})
