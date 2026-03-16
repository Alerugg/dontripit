import test from 'node:test'
import assert from 'node:assert/strict'

const originalEnv = {
  INTERNAL_API_BASE_URL: process.env.INTERNAL_API_BASE_URL,
  INTERNAL_API_KEY: process.env.INTERNAL_API_KEY,
  BACKEND_API_KEY: process.env.BACKEND_API_KEY,
  API_KEY: process.env.API_KEY,
}

test('callInternalApi uses fallback env key when INTERNAL_API_KEY is missing', async () => {
  process.env.INTERNAL_API_BASE_URL = 'http://backend:5000'
  process.env.INTERNAL_API_KEY = ''
  process.env.BACKEND_API_KEY = 'fallback-key'
  process.env.API_KEY = ''

  const calls = []
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), headers: options.headers })
    return {
      ok: true,
      status: 200,
      json: async () => ({ items: [] }),
    }
  }

  const { callInternalApi } = await import(`../lib/catalog/internalApi.js?ts=${Date.now()}`)
  const response = await callInternalApi('/api/v1/search', { params: { q: 'nami', game: 'onepiece' } })

  assert.equal(response.ok, true)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].headers['X-API-Key'], 'fallback-key')
})

test.after(() => {
  process.env.INTERNAL_API_BASE_URL = originalEnv.INTERNAL_API_BASE_URL
  process.env.INTERNAL_API_KEY = originalEnv.INTERNAL_API_KEY
  process.env.BACKEND_API_KEY = originalEnv.BACKEND_API_KEY
  process.env.API_KEY = originalEnv.API_KEY
})
