'use client'

import { useEffect, useMemo, useState } from 'react'
import AppShell from '../../components/AppShell'
import { saveApiKey, readStoredApiKey } from '../../lib/apiKeyStorage'
import { buildApiPath, DEFAULT_LIMIT, DEFAULT_OFFSET, parseNumberInput } from '../console/consoleUtils'

const ACTIONS = [
  { key: 'health', label: 'Health' },
  { key: 'games', label: 'Games' },
  { key: 'search', label: 'Search' },
  { key: 'cardById', label: 'Card by ID' },
  { key: 'printById', label: 'Print by ID' },
]

export default function ApiConsolePage() {
  const [apiKey, setApiKey] = useState('')
  const [game, setGame] = useState('pokemon')
  const [query, setQuery] = useState('charizard')
  const [cardId, setCardId] = useState('1')
  const [printId, setPrintId] = useState('1')
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [offset, setOffset] = useState(DEFAULT_OFFSET)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  useEffect(() => {
    setApiKey(readStoredApiKey())
  }, [])

  useEffect(() => {
    saveApiKey(apiKey.trim())
  }, [apiKey])

  const commonParams = useMemo(
    () => ({ game: game.trim(), q: query.trim(), limit, offset, cardId: cardId.trim(), printId: printId.trim() }),
    [game, query, limit, offset, cardId, printId]
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
        error: response.ok ? null : payload?.detail || payload?.error || 'Error desconocido',
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
    <AppShell>
      <main className="explorer-page">
        <section className="panel hero">
          <p className="eyebrow">API Console</p>
          <h1>Panel secundario para pruebas manuales de endpoints</h1>
          <p className="subtle">Diseñado para desarrollo: guarda tu API key local y prueba request/response sin romper el look del producto.</p>
        </section>

        <section className="panel console-grid">
          <label className="field-label">API Key (X-API-Key)
            <input className="search-input" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="ak_..." />
          </label>
          <label className="field-label">Game
            <select value={game} onChange={(e) => setGame(e.target.value)}>
              <option value="pokemon">pokemon</option>
              <option value="mtg">mtg</option>
              <option value="yugioh">yugioh</option>
            </select>
          </label>
          <label className="field-label">Search query (q)
            <input className="search-input" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label className="field-label">Card ID
            <input className="search-input" value={cardId} onChange={(e) => setCardId(e.target.value)} />
          </label>
          <label className="field-label">Print ID
            <input className="search-input" value={printId} onChange={(e) => setPrintId(e.target.value)} />
          </label>
          <label className="field-label">limit
            <input className="search-input" value={limit} onChange={(e) => setLimit(parseNumberInput(e.target.value, DEFAULT_LIMIT))} />
          </label>
          <label className="field-label">offset
            <input className="search-input" value={offset} onChange={(e) => setOffset(parseNumberInput(e.target.value, DEFAULT_OFFSET))} />
          </label>
        </section>

        <section className="panel">
          <div className="console-actions">
            {ACTIONS.map((action) => (
              <button key={action.key} className="ghost-btn" onClick={() => runAction(action.key)} disabled={loading}>
                {action.label}
              </button>
            ))}
          </div>
        </section>

        {result && (
          <section className="panel">
            <p><strong>URL:</strong> {result.url}</p>
            <p><strong>Status:</strong> {String(result.status)}</p>
            <p><strong>Tiempo:</strong> {result.elapsedMs} ms</p>
            {result.error && <p className="state-panel error"><strong>Error:</strong> {result.error}</p>}
            <pre className="json-viewer">{JSON.stringify(result.payload, null, 2)}</pre>
          </section>
        )}
      </main>
    </AppShell>
  )
}
