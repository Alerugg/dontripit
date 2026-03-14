'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import AppShell from '../components/AppShell'
import ExplorerSidebar from '../components/ExplorerSidebar'
import ResultsGrid from '../components/ResultsGrid'
import { fetchGames, getApiBaseUrlLabel, searchCatalog } from '../lib/apiClient'
import { readStoredApiKey } from '../lib/apiKeyStorage'

const INITIAL_QUERY = 'charizard'

export default function HomeExplorerPage() {
  const [query, setQuery] = useState(INITIAL_QUERY)
  const [selectedGame, setSelectedGame] = useState('')
  const [selectedType, setSelectedType] = useState('')
  const [viewMode, setViewMode] = useState('grid')

  const [games, setGames] = useState([])
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const apiBaseUrl = useMemo(() => getApiBaseUrlLabel(), [])
  const hasRuntimeKey = useMemo(() => Boolean(readStoredApiKey() || process.env.NEXT_PUBLIC_API_KEY), [])

  useEffect(() => {
    fetchGames().then(setGames).catch(() => setGames([]))
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => {
      setLoading(true)
      setError('')

      searchCatalog({ q: query, game: selectedGame, type: selectedType, limit: 30 })
        .then((items) => setResults(items || []))
        .catch((requestError) => {
          setResults([])
          setError(requestError.message)
        })
        .finally(() => setLoading(false))
    }, 280)

    return () => clearTimeout(timer)
  }, [query, selectedGame, selectedType])

  return (
    <AppShell>
      <main className="explorer-page">
        <section className="hero panel">
          <p className="eyebrow">Multi-game TCG Catalog Explorer</p>
          <h1>Descubre cartas, variantes y prints en una experiencia tipo companion app.</h1>
          <p className="subtle">Conectado a la API real · base URL: <strong>{apiBaseUrl}</strong></p>
          {!hasRuntimeKey && (
            <div className="api-key-warning">
              No se detectó API key local. Puedes configurarla desde <Link href="/api-console">API Console</Link> para evitar errores de autenticación.
            </div>
          )}
        </section>

        <div className="explorer-layout">
          <ExplorerSidebar
            query={query}
            onQueryChange={setQuery}
            selectedGame={selectedGame}
            onGameChange={setSelectedGame}
            selectedType={selectedType}
            onTypeChange={setSelectedType}
            games={games}
            viewMode={viewMode}
            onViewModeChange={setViewMode}
          />

          <section className="results-panel panel">
            <div className="results-header">
              <h2>Resultados</h2>
              <span>{loading ? 'Actualizando…' : `${results.length} items`}</span>
            </div>

            {loading && <section className="state-panel">Cargando resultados...</section>}
            {!loading && error && <section className="state-panel error">{error}</section>}
            {!loading && !error && results.length === 0 && <section className="state-panel">No se encontraron resultados con estos filtros.</section>}
            {!loading && !error && results.length > 0 && <ResultsGrid items={results} viewMode={viewMode} />}
          </section>
        </div>
      </main>
    </AppShell>
  )
}
