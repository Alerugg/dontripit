'use client'

import { useEffect, useMemo, useState } from 'react'
import AutocompleteList from '../../components/AutocompleteList'
import CarouselShelf from '../../components/CarouselShelf'
import FiltersBar from '../../components/FiltersBar'
import ResultCard from '../../components/ResultCard'
import SearchBar from '../../components/SearchBar'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { fetchGames, fetchSearch, generateDevApiKey, getApiRuntimeConfig } from '../../lib/apiClient'

const API_KEY_STORAGE = 'tcg_api_key'
const ADMIN_TOKEN_STORAGE = 'tcg_admin_token'
const PAGE_SIZE = 24

export default function ExplorerPage() {
  const [query, setQuery] = useState('')
  const [game, setGame] = useState('')
  const [resultType, setResultType] = useState('')
  const [offset, setOffset] = useState(0)

  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [adminTokenInput, setAdminTokenInput] = useState('')

  const [games, setGames] = useState([])
  const [items, setItems] = useState([])
  const [totalEstimate, setTotalEstimate] = useState(0)
  const [loading, setLoading] = useState(false)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [suggestions, setSuggestions] = useState([])
  const [generatingKey, setGeneratingKey] = useState(false)
  const [featuredShelves, setFeaturedShelves] = useState([])
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const debouncedQuery = useDebouncedValue(query, 350)
  const runtimeConfig = useMemo(() => getApiRuntimeConfig(), [])

  useEffect(() => {
    const storedKey = window.localStorage.getItem(API_KEY_STORAGE) || ''
    const storedAdmin = window.localStorage.getItem(ADMIN_TOKEN_STORAGE) || ''
    setApiKeyInput(storedKey)
    setApiKey(storedKey)
    setAdminTokenInput(storedAdmin)
  }, [])

  useEffect(() => {
    if (!apiKey.trim()) return
    fetchGames(apiKey)
      .then((rows) => setGames(rows || []))
      .catch((requestError) => setError(`No se pudieron cargar juegos: ${requestError.message}`))
  }, [apiKey])

  useEffect(() => {
    if (!apiKey.trim()) return

    const shelves = [
      { title: 'Populares Pokémon', q: 'Charizard', game: 'pokemon' },
      { title: 'Favoritas Yu-Gi-Oh!', q: 'Dark Magician', game: 'yugioh' },
      { title: 'Clásicas MTG', q: 'Forest', game: 'mtg' },
    ]

    Promise.all(
      shelves.map(async (shelf) => {
        const rows = await fetchSearch({ q: shelf.q, game: shelf.game, limit: 12, offset: 0 }, apiKey)
        return {
          title: shelf.title,
          subtitle: `Consulta real: /api/v1/search?q=${encodeURIComponent(shelf.q)}&game=${shelf.game}`,
          items: rows || [],
        }
      })
    )
      .then((result) => setFeaturedShelves(result))
      .catch(() => setFeaturedShelves([]))
  }, [apiKey])

  useEffect(() => {
    if (!apiKey.trim()) return
    setLoading(true)
    setError('')

    fetchSearch({ q: debouncedQuery || 'card', game, type: resultType || undefined, limit: PAGE_SIZE, offset }, apiKey)
      .then((rows) => {
        setItems(rows || [])
        setTotalEstimate((rows || []).length < PAGE_SIZE ? offset + (rows || []).length : offset + PAGE_SIZE + 1)
      })
      .catch((requestError) => {
        setItems([])
        setError(`Error al consultar /search: ${requestError.message}`)
      })
      .finally(() => setLoading(false))
  }, [apiKey, debouncedQuery, game, resultType, offset])

  useEffect(() => {
    if (!apiKey.trim()) return
    if (!debouncedQuery || debouncedQuery.length < 2) {
      setSuggestions([])
      return
    }

    setLoadingSuggestions(true)
    fetchSearch({ q: debouncedQuery, game, type: resultType || undefined, limit: 8, offset: 0 }, apiKey)
      .then((rows) => setSuggestions(rows || []))
      .catch(() => setSuggestions([]))
      .finally(() => setLoadingSuggestions(false))
  }, [apiKey, debouncedQuery, game, resultType])

  function saveApiKey() {
    window.localStorage.setItem(API_KEY_STORAGE, apiKeyInput)
    setApiKey(apiKeyInput)
    setInfo('API key guardada y aplicada para las búsquedas.')
    setOffset(0)
  }

  function saveAdminToken() {
    window.localStorage.setItem(ADMIN_TOKEN_STORAGE, adminTokenInput)
    setInfo('Admin token guardado para generar claves desde frontend.')
  }

  async function handleGenerateApiKey() {
    setGeneratingKey(true)
    setError('')
    setInfo('')
    try {
      const payload = await generateDevApiKey(adminTokenInput)
      const createdKey = payload?.api_key || ''
      if (!createdKey) throw new Error('No se recibió api_key al generar clave')

      setApiKeyInput(createdKey)
      setApiKey(createdKey)
      window.localStorage.setItem(API_KEY_STORAGE, createdKey)
      setInfo('Clave generada correctamente y aplicada automáticamente en el catálogo.')
    } catch (requestError) {
      setError(`No se pudo generar API key: ${requestError.message}`)
    } finally {
      setGeneratingKey(false)
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-7xl p-6">
      <header className="mb-6 rounded-2xl border border-slate-200 bg-gradient-to-r from-white to-slate-100 p-5 shadow-sm">
        <h1 className="text-3xl font-bold text-slate-900">TCG Marketplace Catalog</h1>
        <p className="text-sm text-slate-600">Estilo catálogo informativo, previsualizaciones en carrusel y búsqueda en tiempo real sobre la API real.</p>
      </header>

      <section className="mb-4 grid gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-2">
        <label className="text-sm font-medium text-slate-700">Admin token (para generar key)
          <input type="password" value={adminTokenInput} onChange={(event) => setAdminTokenInput(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2" placeholder="dev_admin_123" />
        </label>
        <div className="flex items-end gap-2">
          <button onClick={saveAdminToken} type="button" className="rounded-xl border border-slate-300 px-4 py-2">Guardar token</button>
          <button onClick={handleGenerateApiKey} type="button" className="rounded-xl bg-blue-700 px-4 py-2 text-white disabled:opacity-60" disabled={generatingKey}>
            {generatingKey ? 'Generando...' : 'Generar API Key'}
          </button>
        </div>

        <label className="text-sm font-medium text-slate-700">X-API-Key usada por búsquedas
          <input type="password" value={apiKeyInput} onChange={(event) => setApiKeyInput(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2" placeholder="ak_..." />
        </label>
        <div className="flex items-end">
          <button onClick={saveApiKey} type="button" className="rounded-xl bg-slate-900 px-4 py-2 text-white">Guardar y usar key</button>
        </div>

        <p className="text-xs text-slate-500 md:col-span-2">Base URL activa: <code>{runtimeConfig.baseUrl}</code></p>
      </section>

      {info && <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">{info}</div>}
      {error && <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="mb-6 grid gap-4">
        {featuredShelves.map((shelf) => (
          <CarouselShelf key={shelf.title} title={shelf.title} subtitle={shelf.subtitle} items={shelf.items} />
        ))}
      </section>

      <section className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="relative">
          <SearchBar value={query} onChange={(value) => { setQuery(value); setOffset(0) }} placeholder="Busca por nombre, collector number, set code..." />
          <AutocompleteList items={suggestions} loading={loadingSuggestions} query={query} onSelect={() => setSuggestions([])} />
        </div>
        <div className="mt-4">
          <FiltersBar
            games={games}
            game={game}
            onGameChange={(value) => { setGame(value); setOffset(0) }}
            resultType={resultType}
            onResultTypeChange={(value) => { setResultType(value); setOffset(0) }}
          />
        </div>
      </section>

      {loading && <p className="mb-4 text-sm text-slate-600">Cargando resultados...</p>}
      {!loading && items.length === 0 && <p className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-slate-500">Sin resultados para estos filtros.</p>}

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((item) => <ResultCard key={`${item.type}-${item.id}`} item={item} />)}
      </section>

      <footer className="mt-6 flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4">
        <button type="button" onClick={() => setOffset((current) => Math.max(current - PAGE_SIZE, 0))} disabled={offset === 0 || loading} className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-50">Anterior</button>
        <p className="text-sm text-slate-600">Offset: {offset} · Mostrando: {items.length}</p>
        <button type="button" onClick={() => setOffset((current) => current + PAGE_SIZE)} disabled={loading || totalEstimate <= offset + PAGE_SIZE} className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-50">Siguiente</button>
      </footer>
    </main>
  )
}
