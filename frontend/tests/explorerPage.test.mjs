import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('explorer page exposes endpoint controls', async () => {
  const page = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')
  assert.match(page, /API Explorer/)
  assert.match(page, /type="password"/)
  assert.match(page, /Save/)
  assert.match(page, /Send request/)
  assert.match(page, /\/api\/v1\/games/)
  assert.match(page, /\/api\/v1\/search/)
  assert.match(page, /\/api\/v1\/cards/)
  assert.match(page, /\/api\/v1\/prints/)
  assert.match(page, /\/api\/health/)
})

test('explorer has json viewer and timeout helper', async () => {
  const jsonViewer = await fs.readFile(new URL('../app/explorer/JsonViewer.js', import.meta.url), 'utf8')
  const helper = await fs.readFile(new URL('../app/explorer/fetchWithTimeout.js', import.meta.url), 'utf8')

  assert.match(jsonViewer, /JSON\.stringify/)
  assert.match(helper, /AbortController/)
  assert.match(helper, /clearTimeout/)
})
