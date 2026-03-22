'use client'

import { useEffect, useMemo, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import GameSearchBar from './GameSearchBar'
import GameProductTypePicker from './GameProductTypePicker'
import GameResultsGrid from './GameResultsGrid'
import GameCollectionsList from './GameCollectionsList'
import GameTournamentsByRegion from './GameTournamentsByRegion'
import GameNewsGrid from './GameNewsGrid'
import StatePanel from '../catalog/StatePanel'
import { buildMasterCards } from '../../lib/catalog/normalizers/search'
import { searchCatalog, suggestCatalog } from '../../lib/catalog/client'

function queryStateFromParams(searchParams) {
  return {
    q: searchParams.get('q') || '',
    type: searchParams.get('type') || 'singles',
    view: searchParams.get('view') || 'grid',
  }
}

export default function GameExplorerPage({ game, collections = [], tournaments = [], news = [] }) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const initialState = useMemo(() => queryStateFromParams(searchParams), [searchParams])
  const [query, setQuery] = useState(initialState.q)
  const [submittedQuery, setSubmittedQuery] = useState(initialState.q)
  const [productType, setProductType] = useState(initialState.type)
  const [view, setView] = useState(initialState.view)
  const [items, setItems] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const [error, setError] = useState('')
  const scrollKey = `${game.slug}:${submittedQuery || query}:${productType}`

  useEffect(() => {
    setQuery(initialState.q)
    setSubmittedQuery(initialState.q)
    setProductType(initialState.type)
    setView(initialState.view)
  }, [initialState])

  useEffect(() => {
    if (!query.trim()) {
      setSuggestions([])
      return undefined
    }

    let cancelled = false
    const handle = setTimeout(async () => {
      setSuggestionsLoading(true)
      try {
        const suggestionItems = await suggestCatalog({ q: query.trim(), game: game.slug, limit: 8 })
        if (!cancelled) setSuggestions(buildMasterCards(suggestionItems).slice(0, 8))
      } catch {
        if (!cancelled) setSuggestions([])
      } finally {
        if (!cancelled) setSuggestionsLoading(false)
      }
    }, 220)

    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [game.slug, query])

  useEffect(() => {
    if (!submittedQuery.trim()) {
      setItems([])
      setError('')
      return undefined
    }

    let cancelled = false

    async function loadResults() {
      setLoading(true)
      setError('')
      try {
        const resultItems = await searchCatalog({ q: submittedQuery.trim(), game: game.slug, type: 'card', limit: 48, offset: 0 })
        if (!cancelled) setItems(buildMasterCards(resultItems))
      } catch (requestError) {
        if (!cancelled) {
          setItems([])
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadResults()
    return () => {
      cancelled = true
    }
  }, [game.slug, submittedQuery])

  useEffect(() => {
    const nextParams = new URLSearchParams()
    if (submittedQuery.trim()) nextParams.set('q', submittedQuery.trim())
    if (productType) nextParams.set('type', productType)
    if (view !== 'grid') nextParams.set('view', view)
    router.replace(nextParams.toString() ? `${pathname}?${nextParams.toString()}` : pathname, { scroll: false })
  }, [pathname, productType, router, submittedQuery, view])

  useEffect(() => {
    const savedScroll = sessionStorage.getItem(`scroll:${scrollKey}`)
    if (savedScroll) {
      requestAnimationFrame(() => window.scrollTo({ top: Number(savedScroll), behavior: 'auto' }))
    }

    const handleScroll = () => sessionStorage.setItem(`scroll:${scrollKey}`, String(window.scrollY))
    window.addEventListener('scroll', handleScroll)
    return () => {
      handleScroll()
      window.removeEventListener('scroll', handleScroll)
    }
  }, [scrollKey])

  const summaryText = submittedQuery
    ? `${items.length} carta${items.length === 1 ? '' : 's'} para “${submittedQuery}” en ${game.name}.`
    : game.description

  return (
    <section className="page-shell game-page">
      <header className="game-hero panel" style={{ '--game-accent': game.accent }}>
        <div>
          <p className="eyebrow">{game.eyebrow}</p>
          <h1>{game.name}</h1>
          <p>{summaryText}</p>
        </div>
        <div className="game-hero-meta panel-soft">
          <article className="hero-card hero-card-b panel-soft">
            <span>Explorador dedicado</span>
            <strong>Busca cartas, sellado y colecciones desde una sola ruta.</strong>
            <small>El estado de búsqueda se conserva al navegar y las variantes viven dentro de cada carta.</small>
          </article>
        </div>
      </header>

      <section className="game-section panel-soft">
        <div className="section-heading compact">
          <p className="eyebrow">Buscador</p>
          <h2>Encuentra cartas sin duplicados por variante.</h2>
        </div>
        <GameSearchBar
          value={query}
          onChange={setQuery}
          onSubmit={() => setSubmittedQuery(query.trim())}
          suggestions={suggestions}
          suggestionsLoading={suggestionsLoading}
          onSuggestionSelect={(item) => {
            setQuery(item.name || item.title || '')
            setSubmittedQuery(item.name || item.title || '')
          }}
          placeholder={`Busca dentro de ${game.name}`}
        />
        <div className="toolbar-row">
          <div className="segmented">
            <button type="button" className={view === 'grid' ? 'segmented-active' : ''} onClick={() => setView('grid')}>Grid</button>
            <button type="button" className={view === 'list' ? 'segmented-active' : ''} onClick={() => setView('list')}>Lista</button>
          </div>
        </div>
      </section>

      <GameProductTypePicker value={productType} onChange={setProductType} />

      {!submittedQuery && <StatePanel title={`Empieza a explorar ${game.name}`} description="Escribe un término para cargar cartas maestras, luego entra al detalle para ver variantes." />}
      {submittedQuery && loading && <StatePanel title="Cargando resultados" description="Buscando cartas maestras con estado persistente." />}
      {submittedQuery && !loading && error && <StatePanel title="No pudimos cargar el catálogo" description={error} error />}
      {submittedQuery && !loading && !error && items.length === 0 && <StatePanel title="Sin resultados" description="Prueba otro nombre, colección o tipo de producto." />}
      {submittedQuery && !loading && !error && items.length > 0 && (
        <GameResultsGrid items={items} view={view} queryState={{ q: submittedQuery, type: productType }} />
      )}

      <GameCollectionsList collections={collections} />
      <GameTournamentsByRegion tournaments={tournaments} />
      <GameNewsGrid news={news} />
    </section>
  )
}
