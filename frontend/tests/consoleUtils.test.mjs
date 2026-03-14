import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('legacy console route redirects to admin api console', async () => {
  const page = await fs.readFile(new URL('../app/console/page.js', import.meta.url), 'utf8')
  assert.match(page, /redirect\('\/admin\/api-console'\)/)
})

test('admin api console includes endpoint presets and response viewer', async () => {
  const page = await fs.readFile(new URL('../app/admin/api-console/page.js', import.meta.url), 'utf8')
  assert.match(page, /Search/)
  assert.match(page, /Card by ID/)
  assert.match(page, /Print by ID/)
  assert.match(page, /Response Viewer/)
})

test('admin middleware requires configured credentials', async () => {
  const middleware = await fs.readFile(new URL('../middleware.js', import.meta.url), 'utf8')
  assert.match(middleware, /ADMIN_CONSOLE_USERNAME/)
  assert.match(middleware, /ADMIN_CONSOLE_PASSWORD/)
  assert.match(middleware, /WWW-Authenticate/)
})
