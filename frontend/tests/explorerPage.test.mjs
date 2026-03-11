import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('explorer page includes catalog + key generation flow', async () => {
  const page = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')

  assert.match(page, /TCG Marketplace Catalog/)
  assert.match(page, /Generar API Key/)
  assert.match(page, /Admin token \(para generar key\)/)
  assert.match(page, /generateDevApiKey/)
  assert.match(page, /CarouselShelf/)
  assert.match(page, /AutocompleteList/)
  assert.match(page, /fetchSearch\(/)
  assert.match(page, /window\.localStorage\.setItem\(API_KEY_STORAGE, createdKey\)/)
})

test('api client supports admin key generation and X-API-Key', async () => {
  const apiClient = await fs.readFile(new URL('../lib/apiClient.js', import.meta.url), 'utf8')

  assert.match(apiClient, /NEXT_PUBLIC_API_BASE_URL/)
  assert.match(apiClient, /X-API-Key/)
  assert.match(apiClient, /\/api\/admin\/dev\/api-keys/)
  assert.match(apiClient, /X-Admin-Token/)
  assert.match(apiClient, /\/api\/v1\/search/)
})
