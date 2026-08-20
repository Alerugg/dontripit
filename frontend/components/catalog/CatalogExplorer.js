'use client'

import { useEffect, useMemo, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import CatalogResults from './ResultsGrid'
import StatePanel from './StatePanel'
import SearchBar from './SearchBar'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { searchCatalogPage as searchCatalog, suggestCatalog } from '../../lib/catalog/client'
import { GAME_OPTIONS, getGameConfig } from '../../lib/catalog/games'
import { getCardHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'

const PAGE_SIZE = 24

const RESULT_TYPES = [
  { value: 'card', label: 'Cartas', countKey: 'card' },
  { value: 'print', label: 'Impresiones', countKey: 'print' },
  { value: 'set', label: 'Sets', countKey: 'set' },
  { value: '', label: 'Todos', countKey: 'all' },
]

const SORT_OPTIONS = [
  { value: 'relevance', label: 'Relevancia', kinds: ['', 'card', 'print', 'set'] },
  { value: 'price_desc', label: 'Precio exacto: mayor a menor', kinds: ['', 'print'] },
  { value: 'price_asc', label: 'Precio exacto: menor a mayor', kinds: ['', 'print'] },
  { value: 'collector_asc', label: 'Numeración: ascendente', kinds: ['', 'card', 'print'] },
  { value: 'collector_desc', label: 'Numeración: descendente', kinds: ['', 'card', 'print'] },
  { value: 'name_asc', label: 'Nombre: A–Z', kinds: ['', 'card', 'print', 'set'] },
  { value: 'name_desc', label: 'Nombre: Z–A', kinds: ['', 'card', 'print', 'set'] },
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

function pageWindow(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index)
  const values = [0]
  const from = Math.max(1, current - 1)
  const to = Math.min(total - 2, current + 1)
  if (from > 1) values.push('gap-left')
  for (let index = from; index <= to; index += 1) values.push(index)
  if (to < total - 2) values.push('gap-right')
  values.push(total - 1)
  return values
}

export default function CatalogExplorer({
  scopedGame = '',
  heading,
  description,
  kicker,
  allowGameSelect = true,
  compactSidebar = false,
  initialQuery = '',
  initialType = '',
  initialView = 'grid',
  initialSort = 'relevance',
  initialLanguage = '',
  initialGame = '',
  initialPricedOnly = false,
}) {
  const router = useRouter()
  const pathname = usePathname()
  const [inputValue, setInputValue] = useState(initialQuery)
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery)
  const [game, setGame] = useState(scopedGame || initialGame)
  const [type, setType] = useState(initialType)
  const [view, setView] = useState(initialView)
  const [sort, setSort] = useState(initialSort)
  const [language, setLanguage] = useState(initialLanguage)
  const [pricedOnly, setPricedOnly] = useState(Boolean(initialPricedOnly))
  const [page, setPage] = useState(0)
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState({ card: 0, print: 0, set: 0, all: 0 })
  const [truncated, setTruncated] = useState(false)
  const [integrity, setIntegrity] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const debouncedInput = useDebouncedValue(inputValue.trim(), 220)

  useEffect(() => {
    setGame(scopedGame || initialGame)
  }, [initialGame, scopedGame])

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
    return () => { cancelled = true }
  }, [debouncedInput, game, scopedGame])

  useEffect(() => {
    if (!submittedQuery) {
      setItems([])
      setTotal(0)
      setCounts({ card: 0, print: 0, set: 0, all: 0 })
      setTruncated(false)
      setIntegrity('')
      setLoading(false)
      setError('')
      return undefined
    }

    let cancelled = false

    async function loadSearchResults() {
      setLoading(true)
      setError('')
      try {
        const result = await searchCatalog({
          q: submittedQuery,
          game: scopedGame || game,
          type,
          language: (type === 'print' || type === '') ? language : '',
          priced: (type === 'print' || type === '') && pricedOnly ? 1 : '',
          sort,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        })
        if (!cancelled) {
          setItems(result.items)
          setTotal(result.total)
          setCounts(result.counts)
          setTruncated(result.truncated)
          setIntegrity(result.integrity || '')
        }
      } catch (requestError) {
        if (!cancelled) {
          setItems([])
          setTotal(0)
          setCounts({ card: 0, print: 0, set: 0, all: 0 })
          setTruncated(false)
          setIntegrity('')
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadSearchResults()
    return () => { cancelled = true }
  }, [submittedQuery, game, scopedGame, type, language, pricedOnly, sort, page])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const params = new URLSearchParams()
    if (submittedQuery) params.set('q', submittedQuery)
    if (type) params.set('kind', type)
    if (view !== 'grid') params.set('view', view)
    if (!scopedGame && game) params.set('game', game)
    if (sort !== 'relevance') params.set('sort', sort)
    if (language && (type === 'print' || type === '')) params.set('language', language)
    if (pricedOnly && (type === 'print' || type === '')) params.set('priced', '1')
    if (page > 0) params.set('page', String(page + 1))
    const next = params.toString()
    window.history.replaceState(window.history.state, '', next ? `${pathname}?${next}` : pathname)
  }, [game, language, page, pathname, pricedOnly, scopedGame, sort, submittedQuery, type, view])

  const currentGame = scopedGame || game
  const currentGameConfig = getGameConfig(currentGame)
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const start = safePage * PAGE_SIZE
  const availableSorts = useMemo(() => SORT_OPTIONS.filter((option) => option.kinds.includes(type)), [type])

  const summaryText = useMemo(() => {
    if (!submittedQuery) return description
    const scope = currentGame ? ` en ${currentGameConfig?.name || currentGame}` : ''
    const loadNote = truncated ? ' · límite de seguridad alcanzado; no extrapolamos resultados' : ''
    return `${total} resultado${total === 1 ? '' : 's'} para “${submittedQuery}”${scope}${loadNote}.`
  }, [description, submittedQuery, total, truncated, currentGame, currentGameConfig])

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

  const submitFullSearch = () => {
    const clean = inputValue.trim()
    if (!clean) return
    if (!submittedQuery && !type) {
      setType('card')
      setView('grid')
      setSort('relevance')
    }
    setSubmittedQuery(clean)
  }

  const changeType = (nextType) => {
    setType(nextType)
    setPage(0)
    if (nextType === 'card') setView('grid')
    if (nextType !== 'print' && nextType !== '') {
      setPricedOnly(false)
      setLanguage('')
    }
    const nextSorts = SORT_OPTIONS.filter((option) => option.kinds.includes(nextType))
    if (!nextSorts.some((option) => option.value === sort)) setSort('relevance')
  }

  const physicalFiltersActive = type === 'print' || type === ''

  return (
    <section className={`catalog-shell explorer-layout ${compactSidebar ? 'explorer-layout-compact' : ''}`}>
      <aside className="catalog-sidebar panel" aria-label="Filtros del catálogo">
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
          <label className="filter-label">Ordenar</label>
          <select className="input" value={sort} onChange={(event) => setSort(event.target.value)}>
            {availableSorts.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        <div className="filter-group">
          <label className="filter-label">Idioma de impresión</label>
          <select className="input" value={language} disabled={!physicalFiltersActive} onChange={(event) => setLanguage(event.target.value)}>
            {LANGUAGE_OPTIONS.map((option) => (
              <option key={option.value || 'all'} value={option.value}>{option.label}</option>
            ))}
          </select>
          {!physicalFiltersActive ? <small className="v6-filter-hint">El idioma pertenece a la impresión física, no a la carta canónica.</small> : null}
        </div>

        <div className="filter-group">
          <label className="checkbox-row">
            <input type="checkbox" checked={pricedOnly} disabled={!physicalFiltersActive} onChange={(event) => setPricedOnly(event.target.checked)} />
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

        <div className="v5-explorer-search">
          <SearchBar
            value={inputValue}
            onChange={setInputValue}
            onSubmit={submitFullSearch}
            suggestions={suggestions}
            suggestionsLoading={suggestionsLoading}
            onSuggestionSelect={handleSuggestionSelect}
            placeholder={currentGame ? `Busca dentro de ${currentGameConfig?.name || currentGame}` : 'Pikachu, Luffy, Black Lotus, Dark Magician…'}
          />
        </div>

        <div className="v5-result-tabs" role="tablist" aria-label="Tipo de resultado">
          {RESULT_TYPES.map((option) => (
            <button
              key={option.value || 'all'}
              type="button"
              role="tab"
              aria-selected={type === option.value}
              className={`v5-result-tab ${type === option.value ? 'is-active' : ''}`}
              onClick={() => changeType(option.value)}
            >
              {option.label}
              <span className="v7-result-count">{counts[option.countKey].toLocaleString()}</span>
            </button>
          ))}
        </div>

        {submittedQuery && !loading && !error && total > 0 ? (
          <div className="v5-result-summary" aria-live="polite">
            <span>
              Mostrando <strong>{start + 1}–{Math.min(start + items.length, total)}</strong> de <strong>{total.toLocaleString()}</strong>
              {truncated ? ' · límite de seguridad visible' : ''}
            </span>
            <span>{type ? RESULT_TYPES.find((item) => item.value === type)?.label : 'Todos los tipos'} · {view === 'grid' ? 'Cuadrícula' : 'Lista'}</span>
          </div>
        ) : null}

        {integrity ? <div className="v7-integrity-note" role="status">{integrity}</div> : null}

        {!submittedQuery && (
          <StatePanel
            title={currentGame ? `Empieza a explorar ${currentGameConfig?.name || currentGame}` : 'Empieza a explorar el catálogo'}
            description="Escribe un nombre como Pikachu o Luffy y pulsa Enter. Verás todas las cartas canónicas coincidentes; después puedes cambiar a impresiones o sets."
          />
        )}
        {submittedQuery && loading && <StatePanel title="Cargando catálogo" description="Consultando la página exacta y los conteos completos de la búsqueda." />}
        {submittedQuery && !loading && error && <StatePanel title="No pudimos cargar el catálogo" description={error || 'Intenta de nuevo en unos segundos.'} error />}
        {submittedQuery && !loading && !error && total === 0 && <StatePanel title="Sin resultados por ahora" description="Prueba otro término, cambia los filtros o vuelve al explorador global." />}
        {!loading && !error && items.length > 0 && <CatalogResults items={items} view={view} />}

        {!loading && !error && total > PAGE_SIZE && (
          <nav className="panel pagination-row" aria-label="Paginación de resultados">
            <button type="button" className="button button-secondary" disabled={safePage <= 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
              Anterior
            </button>
            {pageWindow(safePage, pageCount).map((value) => (
              typeof value === 'string' ? (
                <span key={value} aria-hidden="true">…</span>
              ) : (
                <button
                  key={value}
                  type="button"
                  className={`v5-page-number ${safePage === value ? 'is-active' : ''}`}
                  aria-label={`Página ${value + 1}`}
                  aria-current={safePage === value ? 'page' : undefined}
                  onClick={() => setPage(value)}
                >
                  {value + 1}
                </button>
              )
            ))}
            <button type="button" className="button button-secondary" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>
              Siguiente
            </button>
          </nav>
        )}
      </div>
    </section>
  )
}
