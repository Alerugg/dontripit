'use client'

import { useEffect, useMemo, useState } from 'react'
import JsonViewer from './JsonViewer'
import { fetchWithTimeout } from './fetchWithTimeout'

const API_KEY_STORAGE_KEY = 'apiKey'
const ADMIN_TOKEN_STORAGE_KEY = 'adminToken'

const ENDPOINTS = {
  search: { label: 'Search', path: '/api/v1/search', requiresKey: true },
  games: { label: 'Games', path: '/api/v1/games', requiresKey: true },
  sets: { label: 'Sets', path: '/api/v1/sets', requiresKey: true },
  cards: { label: 'Cards', path: '/api/v1/cards', requiresKey: true },
  prints: { label: 'Prints', path: '/api/v1/prints', requiresKey: true },
  cardDetail: { label: 'Card detail', path: '/api/v1/cards/:id', requiresKey: true },
  printDetail: { label: 'Print detail', path: '/api/v1/prints/:id', requiresKey: true },
}

function extractErrorMessage(status, payload) {
  if (status === 401 && payload?.error === 'missing_admin_token') return 'Missing admin token'
  if (status === 403 && payload?.error === 'invalid_admin_token') return 'Invalid admin token'
  if (status === 401 && payload?.error === 'missing_api_key') return 'missing_api_key'
  if (status === 401 && payload?.error === 'invalid_api_key') return 'invalid_api_key'
  return payload?.detail || payload?.error || `request_failed_${status}`
}

function ResultTable({ rows }) {
  if (!Array.isArray(rows) || !rows.length || rows.some((row) => !row || typeof row !== 'object' || Array.isArray(row))) {
    return null
  }

  const columns = Array.from(rows.reduce((set, row) => {
    Object.keys(row).forEach((key) => set.add(key))
    return set
  }, new Set()))

  return (
    <div className="overflow-x-auto rounded border border-gray-300">
      <table className="min-w-full border-collapse text-left text-sm">
        <thead className="bg-gray-100">
          <tr>
            {columns.map((column) => (
              <th key={column} className="border-b border-gray-300 px-3 py-2 font-semibold text-gray-800">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.id || 'row'}-${index}`} className="odd:bg-white even:bg-gray-50">
              {columns.map((column) => (
                <td key={`${index}-${column}`} className="border-b border-gray-200 px-3 py-2 text-gray-900">
                  {row[column] === null || row[column] === undefined
                    ? '-'
                    : typeof row[column] === 'object'
                      ? JSON.stringify(row[column])
                      : String(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function ExplorerPage() {
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [adminTokenInput, setAdminTokenInput] = useState('')
  const [adminToken, setAdminToken] = useState('')
  const [endpoint, setEndpoint] = useState('search')
  const [games, setGames] = useState([])
  const [gameSlug, setGameSlug] = useState('pokemon')
  const [q, setQ] = useState('')
  const [setCode, setSetCode] = useState('')
  const [cardId, setCardId] = useState('')
  const [detailId, setDetailId] = useState('')
  const [limit, setLimit] = useState('20')
  const [offset, setOffset] = useState('0')
  const [loadingGames, setLoadingGames] = useState(false)
  const [sending, setSending] = useState(false)
  const [generatingKey, setGeneratingKey] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')
  const [success, setSuccess] = useState('')
  const [statusCode, setStatusCode] = useState(null)
  const [responseData, setResponseData] = useState(null)
  const [requestUrl, setRequestUrl] = useState('')

  const endpointConfig = ENDPOINTS[endpoint]
  const hasKey = useMemo(() => Boolean(apiKey.trim()), [apiKey])

  const showGame = ['search', 'sets', 'cards', 'prints'].includes(endpoint)
  const showQ = ['search', 'sets', 'cards'].includes(endpoint)
  const showPagination = ['search', 'sets', 'cards', 'prints'].includes(endpoint)
  const showSetCode = endpoint === 'prints'
  const showCardId = endpoint === 'prints'
  const showDetailId = ['cardDetail', 'printDetail'].includes(endpoint)

  useEffect(() => {
    const savedApiKey = window.localStorage.getItem(API_KEY_STORAGE_KEY) || ''
    const savedAdminToken = window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || ''
    setApiKeyInput(savedApiKey)
    setApiKey(savedApiKey)
    setAdminTokenInput(savedAdminToken)
    setAdminToken(savedAdminToken)
  }, [])

  useEffect(() => {
    async function loadGames() {
      if (!hasKey) return
      setLoadingGames(true)
      try {
        const response = await fetchWithTimeout('/api/v1/games', { headers: { 'X-API-Key': apiKey.trim() } })
        const payload = await response.json().catch(() => ([]))
        if (!response.ok) {
          setError(`Games: ${extractErrorMessage(response.status, payload)}`)
          return
        }
        const gameList = Array.isArray(payload) ? payload : []
        setGames(gameList)
        setGameSlug((current) => (gameList.some((item) => item.slug === current) ? current : gameList[0]?.slug || 'pokemon'))
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : String(loadError))
      } finally {
        setLoadingGames(false)
      }
    }
    loadGames()
  }, [apiKey, hasKey])

  function saveApiKey() {
    const value = apiKeyInput.trim()
    window.localStorage.setItem(API_KEY_STORAGE_KEY, value)
    setApiKey(value)
    setError('')
  }

  function saveAdminToken() {
    const value = adminTokenInput.trim()
    window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, value)
    setAdminToken(value)
    setError('')
  }

  async function generateApiKey() {
    setGeneratingKey(true)
    setError('')
    try {
      const response = await fetchWithTimeout('/api/admin/dev/api-keys', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(adminToken.trim() ? { 'X-Admin-Token': adminToken.trim() } : {}),
        },
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload?.api_key) {
        setError(extractErrorMessage(response.status, payload))
        return
      }
      const nextApiKey = String(payload.api_key)
      window.localStorage.setItem(API_KEY_STORAGE_KEY, nextApiKey)
      setApiKeyInput(nextApiKey)
      setApiKey(nextApiKey)
      setSuccess('API key generated')
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : String(generateError))
    } finally {
      setGeneratingKey(false)
    }
  }

  function buildRequestPath() {
    const params = new URLSearchParams()

    if (showGame && gameSlug) params.set('game', gameSlug)
    if (showQ && q.trim()) params.set('q', q.trim())
    if (showPagination) {
      if (limit.trim()) params.set('limit', limit.trim())
      if (offset.trim()) params.set('offset', offset.trim())
    }
    if (showSetCode && setCode.trim()) params.set('set_code', setCode.trim())
    if (showCardId && cardId.trim()) params.set('card_id', cardId.trim())

    const path = endpointConfig.path
    const resolvedPath = showDetailId ? path.replace(':id', detailId.trim()) : path
    const queryString = params.toString()
    return queryString ? `${resolvedPath}?${queryString}` : resolvedPath
  }

  async function sendRequest() {
    setSending(true)
    setError('')
    setWarning('')
    setStatusCode(null)
    setResponseData(null)

    if (endpointConfig.requiresKey && !hasKey) {
      setWarning('Please save an API key before sending this request.')
      setSending(false)
      return
    }
    if (showDetailId && !detailId.trim()) {
      setWarning('id is required for detail endpoints.')
      setSending(false)
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

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-4 bg-white p-6 text-gray-900">
      <h1 className="text-3xl font-bold text-gray-900">API Explorer</h1>

      <section className="grid grid-cols-1 gap-3 rounded border border-gray-300 bg-gray-50 p-4 md:grid-cols-[1fr_auto]">
        <label className="flex flex-col gap-1 text-sm text-gray-800">API Key
          <input type="password" className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={apiKeyInput} onChange={(event) => setApiKeyInput(event.target.value)} />
        </label>
        <button className="rounded bg-black px-4 py-2 text-white" onClick={saveApiKey} type="button">Save API Key</button>
        <label className="flex flex-col gap-1 text-sm text-gray-800">Admin token (dev)
          <input type="password" className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={adminTokenInput} onChange={(event) => setAdminTokenInput(event.target.value)} />
        </label>
        <div className="flex flex-wrap items-end gap-2">
          <button className="rounded bg-gray-800 px-4 py-2 text-white" onClick={saveAdminToken} type="button">Save token</button>
          <button className="rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-60" onClick={generateApiKey} type="button" disabled={generatingKey}>
            {generatingKey ? 'Generating...' : 'Generate API Key'}
          </button>
        </div>
      </section>

      {success && <div className="rounded border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-700">{success}</div>}
      {warning && <div className="rounded border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-900">{warning}</div>}
      {error && <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="grid grid-cols-1 gap-3 rounded border border-gray-300 bg-gray-50 p-4 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm text-gray-800">Endpoint
          <select className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={endpoint} onChange={(event) => setEndpoint(event.target.value)}>
            {Object.entries(ENDPOINTS).map(([value, config]) => <option key={value} value={value}>{config.label}</option>)}
          </select>
        </label>

        {showGame && (
          <label className="flex flex-col gap-1 text-sm text-gray-800">Game
            <select className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={gameSlug} onChange={(event) => setGameSlug(event.target.value)} disabled={loadingGames || !games.length}>
              {!games.length && <option value="">Save API key first</option>}
              {games.map((gameOption) => <option key={gameOption.slug} value={gameOption.slug}>{gameOption.name}</option>)}
            </select>
          </label>
        )}

        {showQ && <label className="flex flex-col gap-1 text-sm text-gray-800">q<input className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={q} onChange={(event) => setQ(event.target.value)} /></label>}
        {showSetCode && <label className="flex flex-col gap-1 text-sm text-gray-800">set_code<input className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={setCode} onChange={(event) => setSetCode(event.target.value)} /></label>}
        {showCardId && <label className="flex flex-col gap-1 text-sm text-gray-800">card_id<input className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={cardId} onChange={(event) => setCardId(event.target.value)} /></label>}
        {showDetailId && <label className="flex flex-col gap-1 text-sm text-gray-800">id<input className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={detailId} onChange={(event) => setDetailId(event.target.value)} /></label>}
        {showPagination && <label className="flex flex-col gap-1 text-sm text-gray-800">limit<input className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={limit} onChange={(event) => setLimit(event.target.value)} /></label>}
        {showPagination && <label className="flex flex-col gap-1 text-sm text-gray-800">offset<input className="rounded border border-gray-400 bg-white px-3 py-2 text-gray-900" value={offset} onChange={(event) => setOffset(event.target.value)} /></label>}

        <div className="flex items-end">
          <button type="button" className="rounded bg-black px-4 py-2 text-white disabled:opacity-50" onClick={sendRequest} disabled={sending || loadingGames}>
            {sending ? 'Sending...' : 'Send request'}
          </button>
        </div>
      </section>

      <section className="rounded border border-gray-300 bg-gray-50 p-4 text-sm text-gray-900">
        <p><strong>URL final:</strong> <code>{requestUrl || '-'}</code></p>
        <p><strong>Status:</strong> {statusCode ?? '-'}</p>
      </section>

      <section className="rounded border border-gray-300 bg-gray-50 p-4 text-gray-900">
        <h2 className="mb-2 text-lg font-semibold">Resultado</h2>
        <ResultTable rows={Array.isArray(responseData) ? responseData : []} />
        {(!Array.isArray(responseData) || responseData.length === 0) && <JsonViewer data={responseData ?? {}} />}
      </section>
    </main>
  )
}
