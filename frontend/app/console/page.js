'use client'

import { useEffect, useMemo, useState } from 'react'

import { buildApiPath, DEFAULT_LIMIT, DEFAULT_OFFSET, parseNumberInput } from './consoleUtils'

const STORAGE_KEY = 'api_console_key'

export default function ApiConsolePage() {
  const [apiKey, setApiKey] = useState('')
  const [game, setGame] = useState('pokemon')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [offset, setOffset] = useState(DEFAULT_OFFSET)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored) {
      setApiKey(stored)
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, apiKey)
  }, [apiKey])

  const commonParams = useMemo(
    () => ({ game: game.trim(), q: query.trim(), limit, offset }),
    [game, query, limit, offset]
  )

  async function runAction(action) {
    const path = buildApiPath(action, commonParams)
    const start = performance.now()
    setLoading(true)
    setResult(null)

    try {
      const response = await fetch(path, {
        method: 'GET',
        headers: {
          ...(apiKey.trim() ? { 'X-API-Key': apiKey.trim() } : {}),
        },
      })
      const elapsedMs = Math.round(performance.now() - start)
      const payload = await response.json().catch(() => ({ error: 'Respuesta no JSON' }))

      setResult({
        action,
        url: path,
        status: response.status,
        elapsedMs,
        payload,
        error: response.ok ? null : payload?.error || 'Error desconocido',
      })
    } catch (error) {
      setResult({
        action,
        url: path,
        status: null,
        elapsedMs: Math.round(performance.now() - start),
        payload: null,
        error: error instanceof Error ? error.message : String(error),
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-4 p-6">
      <h1 className="text-3xl font-bold">API Console</h1>
      <p className="text-sm opacity-80">Prueba la API usando el proxy de Next (/api/*).</p>

      <section className="grid grid-cols-1 gap-3 rounded border p-4 md:grid-cols-2">
        <label className="flex flex-col gap-1">
          API Key (X-API-Key)
          <input
            className="rounded border px-3 py-2"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="ak_..."
          />
        </label>

        <label className="flex flex-col gap-1">
          Game
          <select className="rounded border px-3 py-2" value={game} onChange={(e) => setGame(e.target.value)}>
            <option value="pokemon">pokemon</option>
            <option value="mtg">mtg</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 md:col-span-2">
          Search query (q)
          <input
            className="rounded border px-3 py-2"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="pikachu"
          />
        </label>

        <label className="flex flex-col gap-1">
          limit
          <input
            className="rounded border px-3 py-2"
            value={limit}
            onChange={(e) => setLimit(parseNumberInput(e.target.value, DEFAULT_LIMIT))}
          />
        </label>
        <label className="flex flex-col gap-1">
          offset
          <input
            className="rounded border px-3 py-2"
            value={offset}
            onChange={(e) => setOffset(parseNumberInput(e.target.value, DEFAULT_OFFSET))}
          />
        </label>
      </section>

      <section className="flex flex-wrap gap-2">
        <button className="rounded bg-black px-3 py-2 text-white" onClick={() => runAction('health')} disabled={loading}>Health</button>
        <button className="rounded bg-black px-3 py-2 text-white" onClick={() => runAction('games')} disabled={loading}>Games</button>
        <button className="rounded bg-black px-3 py-2 text-white" onClick={() => runAction('search')} disabled={loading}>Search</button>
        <button className="rounded bg-black px-3 py-2 text-white" onClick={() => runAction('cards')} disabled={loading}>Cards</button>
        <button className="rounded bg-black px-3 py-2 text-white" onClick={() => runAction('prints')} disabled={loading}>Prints</button>
      </section>

      {result && (
        <section className="rounded border p-4">
          <p><strong>URL:</strong> {result.url}</p>
          <p><strong>Status:</strong> {String(result.status)}</p>
          <p><strong>Tiempo:</strong> {result.elapsedMs} ms</p>
          {result.error && <p className="mt-2 text-red-600"><strong>Error:</strong> {result.error}</p>}
          <pre className="mt-3 overflow-x-auto rounded bg-gray-100 p-3 text-xs">{JSON.stringify(result.payload, null, 2)}</pre>
        </section>
      )}
    </main>
  )
}
