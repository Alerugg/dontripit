const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.join(__dirname, '..')
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8')

const layout = read('app/layout.js')
const home = read('app/page.js')
const login = read('app/login/page.js')
const register = read('app/register/page.js')
const forgotLayout = read('app/forgot-password/layout.js')
const cookies = read('app/cookies/page.js')
const publicHome = read('components/home/PublicHome.js')

test('auth pages rely on the root title template exactly once', () => {
  assert.match(layout, /template: `%s · \$\{SITE_NAME\}`/)
  assert.match(login, /title: 'Entrar'/)
  assert.doesNotMatch(login, /title: 'Entrar · Don’tRipIt'/)
  assert.match(register, /title: 'Crear cuenta'/)
  assert.doesNotMatch(register, /title: 'Crear cuenta · Don’tRipIt'/)
})

test('password recovery has explicit metadata and remains non-indexable', () => {
  assert.match(forgotLayout, /title: 'Recuperar contraseña'/)
  assert.match(forgotLayout, /description:/)
  assert.match(forgotLayout, /index: false/)
  assert.match(forgotLayout, /follow: false/)
})

test('home publishes a canonical URL while decorative imagery stays hidden from AT', () => {
  assert.match(home, /alternates: \{ canonical: '\/' \}/)
  assert.match(publicHome, /className="v17-print-scene" aria-hidden="true"/)
  assert.match(publicHome, /<img[\s\S]*?src=\{src\}[\s\S]*?alt=""[\s\S]*?\/>/)
  assert.match(publicHome, /className="v17-game-brand" aria-hidden="true"/)
})

test('cookie disclosure matches the current no-optional-cookie implementation', () => {
  assert.match(cookies, /cookie propia estrictamente necesaria/)
  assert.match(cookies, /No utilizamos cookies publicitarias ni cookies de analítica de comportamiento/)
  assert.match(cookies, /¿Por qué no aparece un banner de “Aceptar cookies”\?/)
})
