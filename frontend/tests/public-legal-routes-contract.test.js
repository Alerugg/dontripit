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

test('auth proxy protects known private areas without swallowing unknown routes', () => {
  const proxy = source('proxy.js')

  for (const route of ['/dashboard', '/collection', '/wishlist', '/console', '/profile']) {
    assert.match(proxy, new RegExp(`['\"]${route.replace('/', '\\/')}['\"]`))
  }

  assert.match(proxy, /if \(!hasSession && isPrivatePath\(pathname\)\)/)
  assert.doesNotMatch(proxy, /!hasSession && !isPublicPath\(pathname\)/)
  assert.match(proxy, /Unknown routes must reach Next\.js/)
})

test('the application owns a branded not-found experience', () => {
  const notFound = source('app/not-found.js')
  assert.match(notFound, /404 · Fuera de catálogo/)
  assert.match(notFound, /href="\/explorer"/)
  assert.match(notFound, /href="\/"/)
})
