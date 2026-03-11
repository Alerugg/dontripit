import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('explorer page includes catalog + key generation flow', async () => {
  const page = await fs.readFile(new URL('../app/explorer/page.js', import.meta.url), 'utf8')

  assert.match(page, /Catálogo visual moderno para cartas TCG/)
  assert.match(page, /ApiKeyPanel/)
  assert.match(page, /fetchSuggest\(/)
  assert.match(page, /fetchSearch\(/)
  assert.match(page, /saveApiKey\(/)
  const apiKeyPanel = await fs.readFile(new URL('../components/ApiKeyPanel.js', import.meta.url), 'utf8')
  assert.match(apiKeyPanel, /Generar API Key/)
})

test('api client supports admin key generation and X-API-Key', async () => {
  const apiClient = await fs.readFile(new URL('../lib/apiClient.js', import.meta.url), 'utf8')

  assert.match(apiClient, /NEXT_PUBLIC_API_BASE_URL/)
  assert.match(apiClient, /X-API-Key/)
  assert.match(apiClient, /\/api\/admin\/dev\/api-keys/)
  assert.match(apiClient, /X-Admin-Token/)
  assert.match(apiClient, /\/api\/v1\/search\/suggest/)
})
