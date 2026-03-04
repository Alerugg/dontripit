import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('explorer page exposes endpoint controls', async () => {
  const page = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')
  assert.match(page, /API Explorer/)
  assert.match(page, /type="password"/)
  assert.match(page, /Save/)
  assert.match(page, /Generate API Key/)
  assert.match(page, /Send request/)
  assert.match(page, /\/api\/v1\/games/)
  assert.match(page, /\/api\/v1\/search/)
  assert.match(page, /\/api\/v1\/cards/)
  assert.match(page, /\/api\/v1\/prints/)
  assert.match(page, /\/api\/health/)

  assert.match(page, /gameSlug/)
  assert.match(page, /value=\{gameOption\.slug\}/)
  assert.match(page, /\{gameOption\.name\}/)
  assert.match(page, /params\.set\('game', gameSlug\)/)
  assert.match(page, /window\.localStorage\.setItem\(STORAGE_KEY, value\)/)
  assert.match(page, /window\.localStorage\.setItem\(STORAGE_KEY, nextApiKey\)/)
  assert.match(page, /'X-API-Key': apiKey\.trim\(\)/)
})

test('explorer has json viewer and timeout helper', async () => {
  const jsonViewer = await fs.readFile(new URL('../app/explorer/JsonViewer.js', import.meta.url), 'utf8')
  const helper = await fs.readFile(new URL('../app/explorer/fetchWithTimeout.js', import.meta.url), 'utf8')

  assert.match(jsonViewer, /JSON\.stringify/)
  assert.ok(jsonViewer.includes('bg-[#0b0b0b]'))
  assert.match(helper, /AbortController/)
  assert.match(helper, /clearTimeout/)
})
