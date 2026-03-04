import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('explorer page exposes endpoint controls', async () => {
  const page = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')
  assert.match(page, /API Explorer/)
  assert.match(page, /Save API Key/)
  assert.match(page, /Generate API Key/)
  assert.match(page, /Admin token \(dev\)/)
  assert.match(page, /Send request/)
  assert.match(page, /\/api\/v1\/games/)
  assert.match(page, /\/api\/v1\/search/)
  assert.match(page, /\/api\/v1\/cards/)
  assert.match(page, /\/api\/v1\/prints/)
  assert.match(page, /\/api\/v1\/sets/)
  assert.match(page, /Card detail/)
  assert.match(page, /Print detail/)
  assert.match(page, /set_code/)
  assert.match(page, /card_id/)
  assert.match(page, /URL final/)

  assert.match(page, /params\.set\('game', gameSlug\)/)
  assert.match(page, /params\.set\('set_code', setCode\.trim\(\)\)/)
  assert.match(page, /params\.set\('card_id', cardId\.trim\(\)\)/)
  assert.match(page, /window\.localStorage\.setItem\(API_KEY_STORAGE_KEY, value\)/)
  assert.match(page, /window\.localStorage\.setItem\(ADMIN_TOKEN_STORAGE_KEY, value\)/)
  assert.match(page, /'X-Admin-Token': adminToken\.trim\(\)/)
  assert.match(page, /'X-API-Key': apiKey\.trim\(\)/)
})

test('explorer has json viewer and timeout helper', async () => {
  const jsonViewer = await fs.readFile(new URL('../app/explorer/JsonViewer.js', import.meta.url), 'utf8')
  const helper = await fs.readFile(new URL('../app/explorer/fetchWithTimeout.js', import.meta.url), 'utf8')

  assert.match(jsonViewer, /JSON\.stringify/)
  assert.ok(jsonViewer.includes('bg-white'))
  assert.match(helper, /AbortController/)
  assert.match(helper, /clearTimeout/)
})
