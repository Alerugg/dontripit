'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import SearchControls from '../components/SearchControls'
import ResultsGrid from '../components/ResultsGrid'
import { fetchGames, getApiBaseUrlLabel, searchCatalog } from '../lib/apiClient'

const INITIAL_QUERY = 'charizard'

export default function HomeExplorerPage() {
  const [query, setQuery] = useState(INITIAL_QUERY)
  const [selectedGame, setSelectedGame] = useState('')
  const [selectedType, setSelectedType] = useState('')

  const [games, setGames] = useState([])
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const apiBaseUrl = useMemo(() => getApiBaseUrlLabel(), [])

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
    <main className="explorer-page">
      <section className="hero panel">
        <p className="eyebrow">TCG Multi-Game Explorer</p>
        <h1>Explora cartas de Yu-Gi-Oh!, Pokémon y MTG</h1>
        <p className="subtle">Resultados en tiempo real desde la API · base URL: <strong>{apiBaseUrl}</strong></p>
        <div className="hero-links">
          <Link href="/console" className="ghost-btn">API Console</Link>
        </div>
      </section>

      <SearchControls
        query={query}
        onQueryChange={setQuery}
        selectedGame={selectedGame}
        onGameChange={setSelectedGame}
        selectedType={selectedType}
        onTypeChange={setSelectedType}
        games={games}
      />

      {loading && <section className="panel state-panel">Cargando resultados...</section>}
      {!loading && error && <section className="panel state-panel error">Error: {error}</section>}
      {!loading && !error && results.length === 0 && <section className="panel state-panel">No se encontraron resultados para esta búsqueda.</section>}
      {!loading && !error && results.length > 0 && <ResultsGrid items={results} />}
    </main>
  )
}
