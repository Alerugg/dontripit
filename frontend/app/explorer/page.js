'use client'

import { useEffect, useMemo, useState } from 'react'
import JsonViewer from './JsonViewer'
import { fetchWithTimeout } from './fetchWithTimeout'

const STORAGE_KEY = 'api_explorer_key'

const ENDPOINTS = {
  health: { label: 'Health', path: '/api/health', requiresKey: false },
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
  const [game, setGame] = useState('')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState('10')
  const [offset, setOffset] = useState('0')
  const [loadingGames, setLoadingGames] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [statusCode, setStatusCode] = useState(null)
  const [responseData, setResponseData] = useState(null)
  const [requestUrl, setRequestUrl] = useState('')
  const [responseHeaders, setResponseHeaders] = useState({})

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
        setGame('')
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
          setGame('')
          setError(`Games: ${extractErrorMessage(response.status, payload)}`)
          return
        }

        const gameList = Array.isArray(payload) ? payload : payload?.items || []
        setGames(gameList)
        setGame((current) => (current && gameList.includes(current) ? current : gameList[0] || ''))
      } catch (loadError) {
        setGames([])
        setGame('')
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
  }

  function buildRequestPath() {
    const params = new URLSearchParams()

    if (endpoint !== 'health') {
      if (game) params.set('game', game)
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
    setStatusCode(null)
    setResponseData(null)
    setResponseHeaders({})

    if (endpointConfig.requiresKey && !hasKey) {
      setSending(false)
      setError('missing_api_key')
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
      setResponseHeaders({
        'x-plan': response.headers.get('x-plan') || '',
        'x-ratelimit-remaining': response.headers.get('x-ratelimit-remaining') || '',
        'x-quota-used': response.headers.get('x-quota-used') || '',
      })
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
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-4 p-6">
      <h1 className="text-3xl font-bold">API Explorer</h1>

      <section className="grid grid-cols-1 gap-3 rounded border p-4 md:grid-cols-[1fr_auto]">
        <label className="flex flex-col gap-1">
          API Key
          <input
            type="password"
            className="rounded border px-3 py-2"
            placeholder="ak_..."
            value={apiKeyInput}
            onChange={(event) => setApiKeyInput(event.target.value)}
          />
        </label>
        <button className="rounded bg-black px-4 py-2 text-white" onClick={saveApiKey} type="button">
          Save
        </button>
      </section>

      {error && <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="grid grid-cols-1 gap-3 rounded border p-4 md:grid-cols-2">
        <label className="flex flex-col gap-1">
          Endpoint
          <select
            className="rounded border px-3 py-2"
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

        <label className="flex flex-col gap-1">
          Game
          <select
            className="rounded border px-3 py-2"
            value={game}
            onChange={(event) => setGame(event.target.value)}
            disabled={loadingGames || !games.length}
          >
            {!games.length && <option value="">{hasKey ? 'No games found' : 'Save API key first'}</option>}
            {games.map((gameOption) => (
              <option key={gameOption} value={gameOption}>
                {gameOption}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          q
          <input className="rounded border px-3 py-2" value={q} onChange={(event) => setQ(event.target.value)} />
        </label>

        <label className="flex flex-col gap-1">
          limit
          <input className="rounded border px-3 py-2" value={limit} onChange={(event) => setLimit(event.target.value)} />
        </label>

        <label className="flex flex-col gap-1">
          offset
          <input className="rounded border px-3 py-2" value={offset} onChange={(event) => setOffset(event.target.value)} />
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

      <section className="rounded border p-4">
        <p className="text-sm">
          <strong>URL:</strong> <code>{requestUrl || '-'}</code>
        </p>
        <p className="text-sm">
          <strong>Status:</strong> {statusCode ?? '-'}
        </p>
        <div className="text-sm">
          <strong>Headers:</strong>
          <ul className="ml-5 list-disc">
            <li>x-plan: {responseHeaders['x-plan'] || '-'}</li>
            <li>x-ratelimit-remaining: {responseHeaders['x-ratelimit-remaining'] || '-'}</li>
            <li>x-quota-used: {responseHeaders['x-quota-used'] || '-'}</li>
          </ul>
        </div>
      </section>

      <section className="rounded border p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-lg font-semibold">JSON Response</h2>
          <button type="button" className="rounded border px-3 py-1 text-sm" onClick={copyJson}>
            Copy
          </button>
        </div>
        <JsonViewer data={responseData ?? {}} />
      </section>
    </main>
  )
}
