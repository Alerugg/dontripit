'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import CatalogResults from './ResultsGrid'
import StatePanel from './StatePanel'
import SearchBar from './SearchBar'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { RESULT_TYPE_OPTIONS, searchCatalog, suggestCatalog } from '../../lib/catalog/client'
import { GAME_OPTIONS, getGameConfig } from '../../lib/catalog/games'
import { getCardHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'

const SEARCH_LIMIT = 100
const PAGE_SIZE = 24

const SORT_OPTIONS = [
  { value: 'relevance', label: 'Relevancia' },
  { value: 'price_desc', label: 'Precio: mayor a menor' },
  { value: 'price_asc', label: 'Precio: menor a mayor' },
  { value: 'collector_asc', label: 'Numeración: ascendente' },
  { value: 'collector_desc', label: 'Numeración: descendente' },
  { value: 'name_asc', label: 'Nombre: A–Z' },
  { value: 'name_desc', label: 'Nombre: Z–A' },
]

const LANGUAGE_OPTIONS = [
  { value: '', label: 'Todos los idiomas' },
  { value: 'en', label: 'Inglés' },
  { value: 'es', label: 'Español' },
  { value: 'ja', label: 'Japonés' },
  { value: 'fr', label: 'Francés' },
  { value: 'de', label: 'Alemán' },
  { value: 'it', label: 'Italiano' },
  { value: 'pt', label: 'Portugués' },
]

function resolveSuggestionHref(item) {
  if (item.type === 'print') return getPrintHref(item.id)
  if (item.type === 'set') return item.game && (item.set_code || item.code)
    ? getSetHref(item.game, item.set_code || item.code)
    : ''
  if (item.type === 'card') return getCardHref(item.game, item.card_id || item.id)
  return ''
}

function marketPrice(item) {
  const value = Number(item?.market?.display_price)
  return Number.isFinite(value) ? value : null
}

function compareNullableNumber(left, right, direction = 1) {
  const a = left == null ? null : Number(left)
  const b = right == null ? null : Number(right)
  const aValid = Number.isFinite(a)
  const bValid = Number.isFinite(b)
  if (!aValid && !bValid) return 0
  if (!aValid) return 1
  if (!bValid) return -1
  return (a - b) * direction
}

function compareText(left, right, direction = 1) {
  return String(left || '').localeCompare(String(right || ''), undefined, { numeric: true, sensitivity: 'base' }) * direction
}

export default function CatalogExplorer({
  scopedGame = '',
  heading,
  description,
  kicker,
  allowGameSelect = true,
  compactSidebar = false,
}) {
  const router = useRouter()
  const [inputValue, setInputValue] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [game, setGame] = useState(scopedGame)
  const [type, setType] = useState('')
  const [view, setView] = useState('grid')
  const [sort, setSort] = useState('relevance')
  const [language, setLanguage] = useState('')
  const [pricedOnly, setPricedOnly] = useState(false)
  const [page, setPage] = useState(0)
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
    setPage(0)
  }, [submittedQuery, game, scopedGame, type, sort, language, pricedOnly])

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
        const nextItems = await searchCatalog({
          q: submittedQuery,
          game: scopedGame || game,
          type,
          limit: SEARCH_LIMIT,
          offset: 0,
        })
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
  const currentGameConfig = getGameConfig(currentGame)

  const filteredItems = useMemo(() => {
    const next = items.filter((item) => {
      if (language && String(item?.language || '').toLowerCase() !== language) return false
      if (pricedOnly && marketPrice(item) == null) return false
      return true
    })

    if (sort === 'relevance') return next

    return [...next].sort((a, b) => {
      if (sort === 'price_desc') return compareNullableNumber(marketPrice(a), marketPrice(b), -1)
      if (sort === 'price_asc') return compareNullableNumber(marketPrice(a), marketPrice(b), 1)
      if (sort === 'collector_desc') return compareText(a?.collector_number, b?.collector_number, -1)
      if (sort === 'collector_asc') return compareText(a?.collector_number, b?.collector_number, 1)
      if (sort === 'name_desc') return compareText(a?.title || a?.name, b?.title || b?.name, -1)
      if (sort === 'name_asc') return compareText(a?.title || a?.name, b?.title || b?.name, 1)
      return 0
    })
  }, [items, language, pricedOnly, sort])

  const pageCount = Math.max(1, Math.ceil(filteredItems.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const visibleItems = useMemo(
    () => filteredItems.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [filteredItems, safePage],
  )

  const summaryText = useMemo(() => {
    if (!submittedQuery) return description
    const count = filteredItems.length
    const loadedNote = items.length >= SEARCH_LIMIT ? ` (primeras ${SEARCH_LIMIT} coincidencias)` : ''
    return `${count} resultado${count === 1 ? '' : 's'}${loadedNote} para “${submittedQuery}”${currentGame ? ` en ${currentGameConfig?.name || currentGame}` : ''}.`
  }, [description, submittedQuery, filteredItems.length, items.length, currentGame, currentGameConfig])

  const handleSuggestionSelect = (item) => {
    const title = item.title || item.name || ''
    const href = resolveSuggestionHref(item)
    setInputValue(title)
    setSuggestions([])

    if (href) {
      router.push(href)
      return
    }

    if (title) setSubmittedQuery(title)
  }

  return (
    <section className={`catalog-shell explorer-layout ${compactSidebar ? 'explorer-layout-compact' : ''}`}>
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
            placeholder={currentGame ? `Busca dentro de ${currentGameConfig?.name || currentGame}` : 'Busca por carta, colección, set code...'}
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
          <label className="filter-label">Ordenar</label>
          <select className="input" value={sort} onChange={(event) => setSort(event.target.value)}>
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        <div className="filter-group">
          <label className="filter-label">Idioma</label>
          <select className="input" value={language} onChange={(event) => setLanguage(event.target.value)}>
            {LANGUAGE_OPTIONS.map((option) => (
              <option key={option.value || 'all'} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        <div className="filter-group">
          <label className="checkbox-row">
            <input type="checkbox" checked={pricedOnly} onChange={(event) => setPricedOnly(event.target.checked)} />
            <span>Solo impresiones con precio Cardmarket exacto</span>
          </label>
        </div>

        <div className="filter-group">
          <label className="filter-label">Vista</label>
          <div className="segmented">
            <button type="button" className={view === 'grid' ? 'segmented-active' : ''} onClick={() => setView('grid')}>Grid</button>
            <button type="button" className={view === 'list' ? 'segmented-active' : ''} onClick={() => setView('list')}>Lista</button>
          </div>
        </div>

        {currentGameConfig && (
          <div className="filter-group muted-block panel-soft">
            <p className="filter-label">Scope activo</p>
            <strong>{currentGameConfig.name}</strong>
            <p>{currentGameConfig.description}</p>
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
            title={currentGame ? `Empieza a explorar ${currentGameConfig?.name || currentGame}` : 'Empieza a explorar el catálogo'}
            description="Escribe tu término y pulsa Buscar o Enter para cargar resultados completos sin perder el foco del juego actual."
          />
        )}
        {submittedQuery && loading && <StatePanel title="Cargando catálogo" description="Estamos trayendo resultados y precios Cardmarket exactos para tu búsqueda." />}
        {submittedQuery && !loading && error && <StatePanel title="No pudimos cargar el catálogo" description={error || 'Intenta de nuevo en unos segundos.'} error />}
        {submittedQuery && !loading && !error && filteredItems.length === 0 && <StatePanel title="Sin resultados por ahora" description="Prueba otro término, cambia los filtros o vuelve al explorador global." />}
        {!loading && !error && visibleItems.length > 0 && <CatalogResults items={visibleItems} view={view} />}

        {!loading && !error && filteredItems.length > PAGE_SIZE && (
          <nav className="panel pagination-row" aria-label="Paginación de resultados">
            <button type="button" className="button button-secondary" disabled={safePage <= 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
              Anterior
            </button>
            <span>Página {safePage + 1} de {pageCount}</span>
            <button type="button" className="button button-secondary" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>
              Siguiente
            </button>
          </nav>
        )}
      </div>
    </section>
  )
}
