'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import ApiKeyPanel from '../../components/ApiKeyPanel'
import AutocompleteList from '../../components/AutocompleteList'
import CarouselShelf from '../../components/CarouselShelf'
import FiltersBar from '../../components/FiltersBar'
import ResultCard from '../../components/ResultCard'
import SearchBar from '../../components/SearchBar'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { fetchGames, fetchSearch, fetchSuggest, generateDevApiKey, getApiRuntimeConfig } from '../../lib/apiClient'
import { readStoredAuth, saveAdminToken, saveApiKey } from '../../lib/apiKeyStorage'

const PAGE_SIZE = 24

export default function ExplorerPage() {
  const router = useRouter()
  const runtimeConfig = useMemo(() => getApiRuntimeConfig(), [])

  const [query, setQuery] = useState('')
  const [game, setGame] = useState('')
  const [resultType, setResultType] = useState('')
  const [offset, setOffset] = useState(0)

  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [adminTokenInput, setAdminTokenInput] = useState('')

  const [games, setGames] = useState([])
  const [items, setItems] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const [featuredShelves, setFeaturedShelves] = useState([])

  const [loading, setLoading] = useState(false)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [generatingKey, setGeneratingKey] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [hasMore, setHasMore] = useState(false)

  const debouncedQuery = useDebouncedValue(query, 320)

  useEffect(() => {
    const auth = readStoredAuth()
    setApiKeyInput(auth.apiKey)
    setApiKey(auth.apiKey)
    setAdminTokenInput(auth.adminToken)
  }, [])

  useEffect(() => {
    if (!apiKey.trim()) return
    fetchGames(apiKey).then((rows) => setGames(rows || [])).catch((requestError) => setError(requestError.message))
  }, [apiKey])

  useEffect(() => {
    if (!apiKey.trim()) return
    const shelves = [
      { title: 'Populares Pokémon', subtitle: 'Curado con búsqueda real: Charizard', q: 'Charizard', game: 'pokemon' },
      { title: 'Favoritas Yu-Gi-Oh!', subtitle: 'Curado con búsqueda real: Dark Magician', q: 'Dark Magician', game: 'yugioh' },
      { title: 'Clásicas MTG', subtitle: 'Curado con búsqueda real: Forest', q: 'Forest', game: 'mtg' },
    ]

    Promise.all(shelves.map(async (shelf) => ({ ...shelf, items: await fetchSearch({ q: shelf.q, game: shelf.game, limit: 10 }, apiKey) })))
      .then((rows) => setFeaturedShelves(rows))
      .catch(() => setFeaturedShelves([]))
  }, [apiKey])

  useEffect(() => {
    if (!apiKey.trim()) return
    setLoading(true)
    setError('')

    fetchSearch({ q: debouncedQuery || 'card', game, type: resultType || undefined, limit: PAGE_SIZE + 1, offset }, apiKey)
      .then((rows) => {
        const normalized = rows || []
        setItems(normalized.slice(0, PAGE_SIZE))
        setHasMore(normalized.length > PAGE_SIZE)
      })
      .catch((requestError) => {
        setItems([])
        setHasMore(false)
        setError(`Error de búsqueda: ${requestError.message}`)
      })
      .finally(() => setLoading(false))
  }, [apiKey, debouncedQuery, game, resultType, offset])

  useEffect(() => {
    if (!apiKey.trim()) return
    if (!debouncedQuery || debouncedQuery.length < 2) {
      setSuggestions([])
      setHighlightedIndex(-1)
      return
    }

    setLoadingSuggestions(true)
    fetchSuggest({ q: debouncedQuery, game, limit: 8 }, apiKey)
      .then((rows) => setSuggestions(rows || []))
      .catch(() => setSuggestions([]))
      .finally(() => setLoadingSuggestions(false))
  }, [apiKey, debouncedQuery, game])

  function onSaveApiKey() {
    saveApiKey(apiKeyInput.trim())
    setApiKey(apiKeyInput.trim())
    setOffset(0)
    setInfo('API key guardada y aplicada.')
    setError('')
  }

  function onClearApiKey() {
    saveApiKey('')
    setApiKey('')
    setApiKeyInput('')
    setItems([])
    setInfo('API key limpiada.')
  }

  async function onGenerateApiKey() {
    setGeneratingKey(true)
    setError('')
    setInfo('')
    saveAdminToken(adminTokenInput)
    try {
      const payload = await generateDevApiKey(adminTokenInput)
      const createdKey = payload?.api_key || ''
      if (!createdKey) throw new Error('No se recibió api_key')
      saveApiKey(createdKey)
      setApiKeyInput(createdKey)
      setApiKey(createdKey)
      setInfo('API key generada y aplicada automáticamente.')
    } catch (requestError) {
      setError(`No se pudo generar API key: ${requestError.message}`)
    } finally {
      setGeneratingKey(false)
    }
  }

  function handleSelectSuggestion(item) {
    setSuggestions([])
    setHighlightedIndex(-1)
    if (item.type === 'set') {
      setResultType('set')
      setQuery(item.title)
      setOffset(0)
      return
    }
    router.push(`/explorer/${item.type}/${item.id}`)
  }

  function handleSearchKeyDown(event) {
    if (!suggestions.length) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlightedIndex((current) => (current + 1) % suggestions.length)
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlightedIndex((current) => (current <= 0 ? suggestions.length - 1 : current - 1))
    }
    if (event.key === 'Enter' && highlightedIndex >= 0) {
      event.preventDefault()
      handleSelectSuggestion(suggestions[highlightedIndex])
    }
  }

  return (
    <main className="catalog-page">
      <section className="hero panel">
        <p className="eyebrow">API-PROJECT · TCG Explorer</p>
        <h1>Catálogo visual moderno para cartas TCG</h1>
        <p className="hero-subtitle">Explora cartas, prints y sets con una experiencia marketplace, consumiendo exclusivamente la API real de API-PROJECT.</p>
        <p className="runtime-note">Base URL activa: <code>{runtimeConfig.baseUrl}</code></p>
        <div onKeyDown={handleSearchKeyDown}>
          <SearchBar value={query} onChange={(value) => { setQuery(value); setOffset(0) }} />
          <AutocompleteList
            items={suggestions}
            loading={loadingSuggestions}
            query={query}
            highlightedIndex={highlightedIndex}
            onHover={setHighlightedIndex}
            onSelect={handleSelectSuggestion}
          />
        </div>
      </section>

      <ApiKeyPanel
        adminToken={adminTokenInput}
        onAdminTokenChange={setAdminTokenInput}
        onGenerate={onGenerateApiKey}
        generating={generatingKey}
        apiKeyInput={apiKeyInput}
        onApiKeyInputChange={setApiKeyInput}
        onSaveApiKey={onSaveApiKey}
        onClearApiKey={onClearApiKey}
        status={{ active: Boolean(apiKey.trim()) }}
      />

      {info && <p className="banner ok">{info}</p>}
      {error && <p className="banner error">{error}</p>}

      {featuredShelves.map((shelf) => (
        <CarouselShelf key={shelf.title} title={shelf.title} subtitle={shelf.subtitle} items={shelf.items} />
      ))}

      <section className="panel">
        <div className="panel-header">
          <h2>Explorer</h2>
          <p>Filtra y navega por resultados en formato catálogo.</p>
        </div>
        <FiltersBar
          games={games}
          game={game}
          onGameChange={(value) => { setGame(value); setOffset(0) }}
          resultType={resultType}
          onResultTypeChange={(value) => { setResultType(value); setOffset(0) }}
        />
        {loading && <p className="hint">Cargando resultados...</p>}
        {!loading && items.length === 0 && <p className="hint">Sin resultados con los filtros actuales.</p>}
        <div className="catalog-grid">
          {items.map((item) => <ResultCard key={`${item.type}-${item.id}`} item={item} />)}
        </div>
        <div className="pager">
          <button type="button" className="ghost-btn" onClick={() => setOffset((current) => Math.max(current - PAGE_SIZE, 0))} disabled={offset === 0 || loading}>Anterior</button>
          <span>Offset {offset}</span>
          <button type="button" className="ghost-btn" onClick={() => setOffset((current) => current + PAGE_SIZE)} disabled={loading || !hasMore}>Siguiente</button>
        </div>
      </section>
    </main>
  )
}
