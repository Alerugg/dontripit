import test from 'node:test'
import assert from 'node:assert/strict'

import { buildApiPath, parseNumberInput } from '../app/console/consoleUtils.js'

test('buildApiPath for search includes q, game and pagination', () => {
  const url = buildApiPath('search', { game: 'pokemon', q: 'pika', limit: 10, offset: 5 })
  assert.equal(url, '/api/v1/search?game=pokemon&q=pika&limit=10&offset=5')
})

test('buildApiPath can build card and print by id routes', () => {
  assert.equal(buildApiPath('cardById', { cardId: '123' }), '/api/v1/cards/123')
  assert.equal(buildApiPath('printById', { printId: '555' }), '/api/v1/prints/555')
})

test('parseNumberInput uses fallback for negative values', () => {
  assert.equal(parseNumberInput('-2', 20), 20)
})

test('api-console page exposes required action labels', async () => {
  const fs = await import('node:fs/promises')
  const page = await fs.readFile(new URL('../app/api-console/page.js', import.meta.url), 'utf8')
  assert.match(page, /Health/)
  assert.match(page, /Games/)
  assert.match(page, /Search/)
  assert.match(page, /Card by ID/)
  assert.match(page, /Print by ID/)
})
