import test from 'node:test'
import assert from 'node:assert/strict'

import { buildApiPath, parseNumberInput } from '../app/console/consoleUtils.js'

test('buildApiPath for search includes q, game and pagination', () => {
  const url = buildApiPath('search', { game: 'pokemon', q: 'pika', limit: 10, offset: 5 })
  assert.equal(url, '/api/v1/search?game=pokemon&q=pika&limit=10&offset=5')
})

test('buildApiPath for prints ignores q and keeps game with pagination', () => {
  const url = buildApiPath('prints', { game: 'mtg', q: 'bolt', limit: 5, offset: 0 })
  assert.equal(url, '/api/v1/prints?game=mtg&limit=5&offset=0')
})

test('parseNumberInput uses fallback for negative values', () => {
  assert.equal(parseNumberInput('-2', 20), 20)
})

test('console page exposes required action labels', async () => {
  const fs = await import('node:fs/promises')
  const page = await fs.readFile(new URL('../app/console/page.js', import.meta.url), 'utf8')
  assert.match(page, /Health/)
  assert.match(page, /Games/)
  assert.match(page, /Search/)
  assert.match(page, /Cards/)
  assert.match(page, /Prints/)
})
