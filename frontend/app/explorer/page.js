'use client'

import { useEffect, useMemo, useState } from 'react'
import JsonViewer from './JsonViewer'
import { fetchWithTimeout } from './fetchWithTimeout'

const STORAGE_KEY = 'api_key'

const ENDPOINTS = {
  health: { label: 'Health', path: '/api/health', requiresKey: false },
  games: { label: 'Games', path: '/api/v1/games', requiresKey: true },
  search: { label: 'Search', path: '/api/v1/search', requiresKey: true },
  cards: { label: 'Cards', path: '/api/v1/cards', requiresKey: true },
  prints: { label: 'Prints', path: '/api/v1/prints', requiresKey: true },
}

function extractErrorMessage(status, payload) {
  if (status !== 401) {
    return payload?.error || `request_failed_${status}`
  }

  if (payload?.error === 'missing_api_key') return 'missing_api_key'
  if (payload?.error === 'invalid_api_key') return 'invalid_api_key'
  return 'unauthorized'
}

export default function ExplorerPage() {
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [endpoint, setEndpoint] = useState('search')
  const [games, setGames] = useState([])
  const [gameSlug, setGameSlug] = useState('pokemon')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState('10')
  const [offset, setOffset] = useState('0')
  const [loadingGames, setLoadingGames] = useState(false)
  const [sending, setSending] = useState(false)
  const [generatingKey, setGeneratingKey] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')
  const [statusCode, setStatusCode] = useState(null)
  const [responseData, setResponseData] = useState(null)
  const [requestUrl, setRequestUrl] = useState('')
  const [responseHeaders, setResponseHeaders] = useState([])

  const endpointConfig = ENDPOINTS[endpoint]
  const hasKey = useMemo(() => Boolean(apiKey.trim()), [apiKey])

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY) || ''
    setApiKeyInput(saved)
    setApiKey(saved)
  }, [])

  useEffect(() => {
    async function loadGames() {
      if (!hasKey) {
        setGames([])
        setGameSlug('pokemon')
        return
      }

      setLoadingGames(true)
      setError('')
      try {
        const response = await fetchWithTimeout('/api/v1/games', {
          headers: {
            'X-API-Key': apiKey.trim(),
          },
        })
        const payload = await response.json().catch(() => ({}))

        if (!response.ok) {
          setGames([])
          setGameSlug('pokemon')
          setError(`Games: ${extractErrorMessage(response.status, payload)}`)
          return
        }

        const gameList = Array.isArray(payload) ? payload : payload?.items || []
        setGames(gameList)
        setGameSlug((current) => {
          if (current && gameList.some((gameOption) => gameOption?.slug === current)) {
            return current
          }
          return gameList[0]?.slug || 'pokemon'
        })
      } catch (loadError) {
        setGames([])
        setGameSlug('pokemon')
        setError(loadError instanceof Error ? loadError.message : String(loadError))
      } finally {
        setLoadingGames(false)
      }
    }

    loadGames()
  }, [apiKey, hasKey])

  function saveApiKey() {
    const value = apiKeyInput.trim()
    window.localStorage.setItem(STORAGE_KEY, value)
    setApiKey(value)
    setError('')
    setWarning('')
  }

  async function generateApiKey() {
    setGeneratingKey(true)
    setError('')
    setWarning('')

    try {
      const response = await fetchWithTimeout('/api/admin/api-keys', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      })
      const payload = await response.json().catch(() => ({}))

      if (!response.ok || !payload?.api_key) {
        setError(payload?.error || `request_failed_${response.status}`)
        return
      }

      const nextApiKey = String(payload.api_key)
      window.localStorage.setItem(STORAGE_KEY, nextApiKey)
      setApiKeyInput(nextApiKey)
      setApiKey(nextApiKey)
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : String(generateError))
    } finally {
      setGeneratingKey(false)
    }
  }

  function buildRequestPath() {
    const params = new URLSearchParams()

    if (endpoint !== 'health') {
      if (gameSlug) params.set('game', gameSlug)
      if (q.trim()) params.set('q', q.trim())
      if (limit.trim()) params.set('limit', limit.trim())
      if (offset.trim()) params.set('offset', offset.trim())
    }

    const queryString = params.toString()
    return queryString ? `${endpointConfig.path}?${queryString}` : endpointConfig.path
  }

  async function sendRequest() {
    setSending(true)
    setError('')
    setWarning('')
    setStatusCode(null)
    setResponseData(null)
    setResponseHeaders([])

    if (endpointConfig.requiresKey && !hasKey) {
      setSending(false)
      setWarning('Please save an API key before sending this request.')
      return
    }

    const path = buildRequestPath()
    setRequestUrl(path)

    try {
      const response = await fetchWithTimeout(path, {
        headers: {
          ...(hasKey ? { 'X-API-Key': apiKey.trim() } : {}),
        },
      })
      const payload = await response.json().catch(() => ({}))

      setStatusCode(response.status)

      const headerItems = []
      response.headers.forEach((value, key) => {
        headerItems.push({ key, value })
      })
      setResponseHeaders(headerItems)
      setResponseData(payload)

      if (!response.ok) {
        setError(extractErrorMessage(response.status, payload))
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      setSending(false)
    }
  }

  async function copyJson() {
    if (!responseData) return
    await navigator.clipboard.writeText(JSON.stringify(responseData, null, 2))
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-4 bg-white p-6 text-gray-900">
      <h1 className="text-3xl font-bold text-gray-900">API Explorer</h1>

      <section className="grid grid-cols-1 gap-3 rounded border border-gray-300 bg-gray-50 p-4 md:grid-cols-[1fr_auto_auto]">
        <label className="flex flex-col gap-1 text-sm text-gray-800">
          API Key
          <input
            type="password"
            className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900 placeholder:text-gray-500"
            placeholder="ak_..."
            value={apiKeyInput}
            onChange={(event) => setApiKeyInput(event.target.value)}
          />
        </label>
        <button className="rounded bg-black px-4 py-2 text-white" onClick={saveApiKey} type="button">
          Save
        </button>
        <button
          className="rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-60"
          onClick={generateApiKey}
          type="button"
          disabled={generatingKey}
        >
          {generatingKey ? 'Generating...' : 'Generate API Key'}
        </button>
      </section>

      {warning && (
        <div className="rounded border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-900">{warning}</div>
      )}

      {error && <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="grid grid-cols-1 gap-3 rounded border border-gray-300 bg-gray-50 p-4 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm text-gray-800">
          Endpoint
          <select
            className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900"
            value={endpoint}
            onChange={(event) => setEndpoint(event.target.value)}
          >
            {Object.entries(ENDPOINTS).map(([value, config]) => (
              <option key={value} value={value}>
                {config.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm text-gray-800">
          Game
          <select
            className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900"
            value={games.length ? gameSlug : ''}
            onChange={(event) => setGameSlug(event.target.value)}
            disabled={loadingGames || !games.length}
          >
            {!games.length && <option value="">{hasKey ? 'No games found' : 'Save API key first'}</option>}
            {games.map((gameOption) => (
              <option key={gameOption.slug} value={gameOption.slug}>
                {gameOption.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm text-gray-800">
          q
          <input
            className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900"
            value={q}
            onChange={(event) => setQ(event.target.value)}
          />
        </label>

        <label className="flex flex-col gap-1 text-sm text-gray-800">
          limit
          <input
            className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900"
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
          />
        </label>

        <label className="flex flex-col gap-1 text-sm text-gray-800">
          offset
          <input
            className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900"
            value={offset}
            onChange={(event) => setOffset(event.target.value)}
          />
        </label>

        <div className="flex items-end">
          <button
            type="button"
            className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
            onClick={sendRequest}
            disabled={sending || loadingGames}
          >
            {sending ? 'Sending...' : 'Send request'}
          </button>
        </div>
      </section>

      <section className="rounded border border-gray-700 bg-[#111111] p-4 text-sm text-gray-100">
        <p>
          <strong>URL:</strong> <code className="text-gray-200">{requestUrl || '-'}</code>
        </p>
        <p>
          <strong>Status:</strong> {statusCode ?? '-'}
        </p>
        <div>
          <strong>Headers:</strong>
          <ul className="ml-5 list-disc">
            {responseHeaders.length === 0 && <li>-</li>}
            {responseHeaders.map((header) => (
              <li key={header.key}>
                {header.key}: {header.value || '-'}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="rounded border border-gray-700 bg-[#111111] p-4 text-gray-100">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Body</h2>
          <button type="button" className="rounded border border-gray-500 px-3 py-1 text-sm text-gray-100" onClick={copyJson}>
            Copy
          </button>
        </div>
        <JsonViewer data={responseData ?? {}} />
      </section>
    </main>
  )
}
