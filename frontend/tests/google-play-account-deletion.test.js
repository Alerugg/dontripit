const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const REPO = path.resolve(ROOT, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')
const repoSource = (file) => fs.readFileSync(path.join(REPO, file), 'utf8')

test('public deletion URL works without authentication and is not indexed', () => {
  const page = source('app/delete-account/page.js')
  assert.match(page, /robots:\s*\{\s*index:\s*false,\s*follow:\s*false\s*\}/)
  assert.match(page, /aunque no puedas iniciar sesión/i)
  assert.match(page, /info@dontripit\.com|PRIVACY_EMAIL/)
  assert.match(page, /Solicitud de eliminación de cuenta Don’tRipIt/)
  assert.match(page, /Nunca envíes tu contraseña/i)
  assert.match(page, /\/dashboard#account-settings/)
})

test('in-app account deletion requires password and explicit destructive confirmation', () => {
  const dashboard = source('components/dashboard/DashboardPage.js')
  const bff = source('app/api/auth/me/route.js')
  assert.match(dashboard, /id="account-settings"/)
  assert.match(dashboard, /Eliminar mi cuenta/)
  assert.match(dashboard, /deleteConfirmation\.trim\(\)\.toUpperCase\(\) === 'ELIMINAR'/)
  assert.match(dashboard, /method:\s*'DELETE'/)
  assert.match(dashboard, /password:\s*deletePassword/)
  assert.match(bff, /callUserApi\('\/api\/v2\/auth\/account'/)
  assert.match(bff, /clearSessionCookie\(response\)/)
})

test('backend hard-deletes the authenticated user and user-owned tables cascade', () => {
  const route = repoSource('backend/app/routes/user_auth.py')
  const models = repoSource('backend/app/user_models.py')
  assert.match(route, /@user_auth_bp\.delete\("\/api\/v2\/auth\/account"\)/)
  assert.match(route, /confirmation != "ELIMINAR"/)
  assert.match(route, /password_matches\(user, password\)/)
  assert.match(route, /session\.delete\(user\)/)
  for (const model of ['UserSession', 'UserPasswordResetToken', 'UserCollectionItem', 'UserWishlistItem']) {
    assert.match(models, new RegExp(`class ${model}`))
  }
  const cascades = models.match(/ForeignKey\("users\.id", ondelete="CASCADE"\)/g) || []
  assert.ok(cascades.length >= 4, `expected at least four user cascade foreign keys, got ${cascades.length}`)
})

test('privacy and footer expose the external account deletion resource', () => {
  const privacy = source('app/privacy/page.js')
  const footer = source('components/layout/SiteFooter.js')
  assert.match(privacy, /href="\/delete-account"/)
  assert.match(privacy, /colección y wishlist/i)
  assert.match(footer, /href="\/delete-account"/)
  assert.match(footer, /Eliminar cuenta/)
})

test('standalone app navigation does not obstruct deletion flow', () => {
  const nav = source('components/pwa/StandaloneNav.js')
  assert.match(nav, /'\/delete-account'/)
})
