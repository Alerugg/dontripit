'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import CatalogResults from './ResultsGrid'
import StatePanel from './StatePanel'
import SearchBar from './SearchBar'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { RESULT_TYPE_OPTIONS, searchCatalog, suggestCatalog } from '../../lib/catalog/client'
import { GAME_OPTIONS, getGameConfig } from '../../lib/catalog/games'

function resolveSuggestionHref(item) {
  if (item.type === 'print') return `/prints/${item.id}`
  if (item.type === 'card') return `/cards/${item.card_id || item.id}`
  return ''
}

export default function CatalogExplorer({ scopedGame = '', heading, description, kicker, allowGameSelect = true }) {
  const router = useRouter()
  const gameConfig = getGameConfig(scopedGame)
  const [inputValue, setInputValue] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [game, setGame] = useState(scopedGame)
  const [type, setType] = useState('')
  const [view, setView] = useState('grid')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const debouncedInput = useDebouncedValue(inputValue.trim(), 220)

  useEffect(() => {
    setGame(scopedGame)
  }, [scopedGame])

  useEffect(() => {
    if (!debouncedInput) {
      setSuggestions([])
      setSuggestionsLoading(false)
      return undefined
    }

    let cancelled = false

    async function loadSuggestions() {
      setSuggestionsLoading(true)
      try {
        const nextSuggestions = await suggestCatalog({ q: debouncedInput, game: scopedGame || game, limit: 8 })
        if (!cancelled) setSuggestions(nextSuggestions)
      } catch {
        if (!cancelled) setSuggestions([])
      } finally {
        if (!cancelled) setSuggestionsLoading(false)
      }
    }

    loadSuggestions()
    return () => {
      cancelled = true
    }
  }, [debouncedInput, game, scopedGame])

  useEffect(() => {
    if (!submittedQuery) {
      setItems([])
      setLoading(false)
      setError('')
      return undefined
    }

    let cancelled = false

    async function loadSearchResults() {
      setLoading(true)
      setError('')
      try {
        const nextItems = await searchCatalog({ q: submittedQuery, game: scopedGame || game, type, limit: 36, offset: 0 })
        if (!cancelled) setItems(nextItems)
      } catch (requestError) {
        if (!cancelled) {
          setItems([])
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadSearchResults()
    return () => {
      cancelled = true
    }
  }, [submittedQuery, game, scopedGame, type])

  const currentGame = scopedGame || game
  const summaryText = useMemo(() => {
    if (!submittedQuery) return description
    const count = items.length
    return `${count} resultado${count === 1 ? '' : 's'} para “${submittedQuery}”${currentGame ? ` en ${gameConfig?.name || currentGame}` : ''}.`
  }, [description, submittedQuery, items.length, currentGame, gameConfig])

  const handleSuggestionSelect = (item) => {
    const title = item.title || item.name || ''
    const href = resolveSuggestionHref(item)
    setInputValue(title)
    setSuggestions([])

    if (href) {
      router.push(href)
      return
    }

    if (title) {
      setSubmittedQuery(title)
    }
  }

  return (
    <section className="catalog-shell explorer-layout">
      <aside className="catalog-sidebar panel">
        <div className="filter-group">
          <label className="filter-label">Buscar cartas / prints / sets</label>
          <SearchBar
            value={inputValue}
            onChange={setInputValue}
            onSubmit={() => setSubmittedQuery(inputValue.trim())}
            suggestions={suggestions}
            suggestionsLoading={suggestionsLoading}
            onSuggestionSelect={handleSuggestionSelect}
            placeholder={currentGame ? `Busca dentro de ${gameConfig?.name || currentGame}` : 'Busca por carta, colección, set code...'}
          />
        </div>

        {allowGameSelect && (
          <div className="filter-group">
            <label className="filter-label">Juego</label>
            <select className="input" value={game} onChange={(event) => setGame(event.target.value)}>
              {GAME_OPTIONS.map((option) => (
                <option key={option.value || 'all'} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        )}

        <div className="filter-group">
          <label className="filter-label">Tipo de resultado</label>
          <select className="input" value={type} onChange={(event) => setType(event.target.value)}>
            {RESULT_TYPE_OPTIONS.map((option) => (
              <option key={option.value || 'all'} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        <div className="filter-group">
          <label className="filter-label">Vista</label>
          <div className="segmented">
            <button type="button" className={view === 'grid' ? 'segmented-active' : ''} onClick={() => setView('grid')}>Grid</button>
            <button type="button" className={view === 'list' ? 'segmented-active' : ''} onClick={() => setView('list')}>Lista</button>
          </div>
        </div>

        {gameConfig && (
          <div className="filter-group muted-block panel-soft">
            <p className="filter-label">Scope activo</p>
            <strong>{gameConfig.name}</strong>
            <p>{gameConfig.description}</p>
          </div>
        )}
      </aside>

      <div className="catalog-main">
        <header className="catalog-header panel hero-mini">
          <p className="kicker">{kicker}</p>
          <h1>{heading}</h1>
          <p>{summaryText}</p>
        </header>

        {!submittedQuery && (
          <StatePanel
            title={currentGame ? `Empieza a explorar ${gameConfig?.name || currentGame}` : 'Empieza a explorar el catálogo'}
            description="Escribe tu término y pulsa Buscar o Enter para cargar resultados completos sin perder el foco del juego actual."
          />
        )}
        {submittedQuery && loading && <StatePanel title="Cargando catálogo" description="Estamos trayendo resultados actualizados para tu búsqueda." />}
        {submittedQuery && !loading && error && <StatePanel title="No pudimos cargar el catálogo" description={error || 'Intenta de nuevo en unos segundos.'} error />}
        {submittedQuery && !loading && !error && items.length === 0 && <StatePanel title="Sin resultados por ahora" description="Prueba otro término, cambia el tipo o vuelve al explorador global." />}
        {!loading && !error && items.length > 0 && <CatalogResults items={items} view={view} />}
      </div>
    </section>
  )
}
