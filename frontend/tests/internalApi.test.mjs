import test from 'node:test'
import assert from 'node:assert/strict'

const originalEnv = {
  INTERNAL_API_BASE_URL: process.env.INTERNAL_API_BASE_URL,
  INTERNAL_API_KEY: process.env.INTERNAL_API_KEY,
  BACKEND_API_KEY: process.env.BACKEND_API_KEY,
  API_KEY: process.env.API_KEY,
}

test('callInternalApi requires INTERNAL_API_KEY and does not fallback to other env vars', async () => {
  process.env.INTERNAL_API_BASE_URL = 'http://backend:5000'
  process.env.INTERNAL_API_KEY = ''
  process.env.BACKEND_API_KEY = 'fallback-key'
  process.env.API_KEY = ''

  const { callInternalApi } = await import(`../lib/catalog/internalApi.js?ts=${Date.now()}`)
  const response = await callInternalApi('/api/v1/search', { params: { q: 'nami', game: 'onepiece' } })

  assert.equal(response.ok, false)
  assert.equal(response.status, 503)
  assert.equal(response.payload.error, 'missing_internal_api_key')
})

test.after(() => {
  process.env.INTERNAL_API_BASE_URL = originalEnv.INTERNAL_API_BASE_URL
  process.env.INTERNAL_API_KEY = originalEnv.INTERNAL_API_KEY
  process.env.BACKEND_API_KEY = originalEnv.BACKEND_API_KEY
  process.env.API_KEY = originalEnv.API_KEY
})
