const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const assert = require('node:assert/strict')

const root = path.resolve(__dirname, '..')

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8')
}

test('password recovery pages remain public for signed-out users', () => {
  const proxy = read('proxy.js')
  assert.match(proxy, /['"]\/forgot-password['"]/)
  assert.match(proxy, /['"]\/reset-password['"]/)

  const forgot = read('app/forgot-password/page.js')
  assert.match(forgot, /Enviar enlace de recuperación/)
  assert.match(forgot, /\/api\/auth\/forgot-password/)

  const reset = read('app/reset-password/page.js')
  assert.match(reset, /\/api\/auth\/reset-password/)
})
