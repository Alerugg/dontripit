'use client'

import './GameExplorerPage.css'
import { useEffect, useMemo, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import GameSearchBar from './GameSearchBar'
import GameProductTypePicker from './GameProductTypePicker'
import GameResultsGrid from './GameResultsGrid'
import GameCollectionsList from './GameCollectionsList'
import GameTournamentsByRegion from './GameTournamentsByRegion'
import GameNewsGrid from './GameNewsGrid'
import StatePanel from '../catalog/StatePanel'
import SectionHeader from '../ui/SectionHeader'
import {
  fetchGamePrints,
  fetchNewsByGame,
  fetchSetsByGame,
  searchCatalog,
  suggestCatalog,
} from '../../lib/catalog/client'
import { buildMasterCards } from '../../lib/catalog/normalizers/search'

function queryStateFromParams(searchParams) {
  return {
    q: searchParams.get('q') || '',
    type: searchParams.get('type') || 'singles',
    view: searchParams.get('view') || 'grid',
  }
}

const PRODUCT_COPY = {
  singles: {
    resultLabel: 'prints',
    resultLabelPlural: 'prints',
    loadingDescription: 'Buscando prints para que abras la carta y revises sus variantes en contexto.',
    emptyDescription: 'Prueba con nombre de carta, número de colección o código de set (ej: OP-01).',
  },
  sealed: {
    resultLabel: 'resultado',
    resultLabelPlural: 'resultados',
    loadingDescription: 'Buscando producto sellado y resultados relacionados en el catálogo.',
    emptyDescription: 'Prueba con nombre de producto, set o expansión.',
  },
  all: {
    resultLabel: 'resultado',
    resultLabelPlural: 'resultados',
    loadingDescription: 'Buscando cartas, sets y producto sellado de forma unificada.',
    emptyDescription: 'Prueba otro nombre o combina carta + set para obtener mejores coincidencias.',
  },
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

  const [collectionsData, setCollectionsData] = useState([])
  const [collectionsLoading, setCollectionsLoading] = useState(false)
  const [collectionsError, setCollectionsError] = useState('')

  const [newsData, setNewsData] = useState(news)
  const [newsLoading, setNewsLoading] = useState(false)
  const [newsError, setNewsError] = useState('')

  const scrollKey = `${game.slug}:${submittedQuery || query}:${productType}`
  const selectedProductCopy = PRODUCT_COPY[productType] || PRODUCT_COPY.all

  useEffect(() => {
    setQuery(initialState.q)
    setSubmittedQuery(initialState.q)
    setProductType(initialState.type)
    setView(initialState.view)
  }, [initialState])

  useEffect(() => {
    let cancelled = false

    async function loadCollections() {
      setCollectionsLoading(true)
      setCollectionsError('')

      try {
        const nextCollections = await fetchSetsByGame(game.slug, { limit: 500 })
        if (!cancelled) setCollectionsData(nextCollections)
      } catch (requestError) {
        if (!cancelled) {
          setCollectionsData([])
          setCollectionsError(requestError.message || 'No pudimos cargar las colecciones.')
        }
      } finally {
        if (!cancelled) setCollectionsLoading(false)
      }
    }

    loadCollections()

    return () => {
      cancelled = true
    }
  }, [game.slug])

  useEffect(() => {
    let cancelled = false

    async function loadNews() {
      setNewsLoading(true)
      setNewsError('')

      try {
        const nextNews = await fetchNewsByGame(game.slug, { limit: 6 })
        if (!cancelled) setNewsData(nextNews)
      } catch (requestError) {
        if (!cancelled) {
          setNewsData([])
          setNewsError(requestError.message || 'No pudimos cargar noticias oficiales.')
        }
      } finally {
        if (!cancelled) setNewsLoading(false)
      }
    }

    loadNews()

    return () => {
      cancelled = true
    }
  }, [game.slug])

  useEffect(() => {
    if (!query.trim()) {
      setSuggestions([])
      return undefined
    }

    let cancelled = false
    const handle = setTimeout(async () => {
      setSuggestionsLoading(true)

      try {
        const suggestionItems = await suggestCatalog({
          q: query.trim(),
          game: game.slug,
          limit: 8,
        })

        if (!cancelled) {
          setSuggestions(buildMasterCards(suggestionItems).slice(0, 8))
        }
      } catch {
        try {
          const fallbackItems = await searchCatalog({
            q: query.trim(),
            game: game.slug,
            type: 'card',
            limit: 8,
            offset: 0,
          })

          if (!cancelled) {
            setSuggestions(buildMasterCards(fallbackItems).slice(0, 8))
          }
        } catch {
          if (!cancelled) setSuggestions([])
        }
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
        const isSinglesMode = productType === 'singles'

        const resultItems = isSinglesMode
          ? await fetchGamePrints({
              game: game.slug,
              q: submittedQuery.trim(),
              limit: 100,
              offset: 0,
            })
          : await searchCatalog({
              game: game.slug,
              q: submittedQuery.trim(),
              limit: 100,
              offset: 0,
            })

        if (!cancelled) {
          setItems(isSinglesMode ? resultItems : buildMasterCards(resultItems))
        }
      } catch (requestError) {
        if (!cancelled) {
          setItems([])
          setError(requestError.message || 'No pudimos cargar el catálogo.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadResults()

    return () => {
      cancelled = true
    }
  }, [game.slug, submittedQuery, productType])

  useEffect(() => {
    const nextParams = new URLSearchParams()
    if (submittedQuery.trim()) nextParams.set('q', submittedQuery.trim())
    if (productType) nextParams.set('type', productType)
    if (view !== 'grid') nextParams.set('view', view)

    router.replace(
      nextParams.toString() ? `${pathname}?${nextParams.toString()}` : pathname,
      { scroll: false },
    )
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
    ? `${items.length} ${items.length === 1 ? selectedProductCopy.resultLabel : selectedProductCopy.resultLabelPlural} para “${submittedQuery}” en ${game.name}.`
    : `Explora cartas maestras, variantes, sets y producto sellado de ${game.name} sin duplicados en la búsqueda.`

  const isPokemonPilot = game.slug === 'pokemon'

  function navigateFromSuggestion(item) {
    const nextQuery = (item.name || item.title || '').trim()
    if (!nextQuery) return false

    if (item.type === 'set' && item.set_code) {
      router.push(`/games/${game.slug}/sets/${String(item.set_code).toLowerCase()}`)
      return true
    }

    const targetCardId = item.card_id || (item.type === 'card' ? item.id : null)
    if ((item.type === 'card' || item.type === 'print') && targetCardId) {
      const nextParams = new URLSearchParams()
      nextParams.set('q', nextQuery)
      nextParams.set('type', 'singles')
      router.push(`/games/${game.slug}/cards/${targetCardId}?${nextParams.toString()}`)
      return true
    }

    return false
  }

  return (
    <section className={`page-shell game-page ${isPokemonPilot ? 'game-page-pilot' : ''}`}>
      <header
        className={`game-hero panel ${isPokemonPilot ? 'game-hero-pilot' : ''}`}
        style={{ '--game-accent': game.accent }}
      >
        <div className="game-hero-copy">
          <p className="eyebrow">{game.eyebrow}</p>
          <h1>{game.name}</h1>
          <p>{summaryText}</p>

          <div className="game-hero-insights">
            <div className="hero-insight-chip">
              <span>Carta</span>
              <strong>Entidad principal</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Variantes</span>
              <strong>Viven dentro del detalle</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Explorer</span>
              <strong>Lectura limpia y sin duplicados</strong>
            </div>
          </div>
        </div>

        <div className={`game-hero-meta panel-soft ${isPokemonPilot ? 'game-hero-meta-pilot' : ''}`}>
          <article className={`hero-card panel-soft ${isPokemonPilot ? 'explorer-preview-card' : 'hero-card-b'}`}>
            <span>Explorador dedicado</span>
            <strong>Busca cartas, sellado y colecciones desde una sola ruta.</strong>
            <small>
              La búsqueda general sigue siendo master-card. Las colecciones abren el checklist real por print.
            </small>
          </article>
        </div>
      </header>

      <section className={`game-section panel-soft ${isPokemonPilot ? 'explorer-search-panel' : ''}`}>
        <SectionHeader
          compact
          eyebrow="Buscador"
          title="Encuentra cartas sin duplicados por variante."
          description="La búsqueda global prioriza cartas maestras. Dentro de las colecciones verás prints exactos del set."
        />

        <GameSearchBar
          value={query}
          onChange={setQuery}
          onSubmit={() => setSubmittedQuery(query.trim())}
          suggestions={suggestions}
          suggestionsLoading={suggestionsLoading}
          onSuggestionSelect={(item) => {
            const nextQuery = item.name || item.title || ''
            setQuery(nextQuery)
            if (!navigateFromSuggestion(item)) {
              setSubmittedQuery(nextQuery)
            }
          }}
          placeholder={`Busca dentro de ${game.name}`}
          variant={isPokemonPilot ? 'pilot' : 'default'}
        />

        <div className={`toolbar-row ${isPokemonPilot ? 'explorer-toolbar' : ''}`}>
          <div className={`segmented ${isPokemonPilot ? 'segmented-pilot' : ''}`}>
            <button
              type="button"
              className={view === 'grid' ? 'segmented-active' : ''}
              onClick={() => setView('grid')}
            >
              Grid
            </button>
            <button
              type="button"
              className={view === 'list' ? 'segmented-active' : ''}
              onClick={() => setView('list')}
            >
              Lista
            </button>
          </div>
        </div>
      </section>

      <GameProductTypePicker value={productType} onChange={setProductType} />

      {submittedQuery && loading && (
        <StatePanel
          title="Cargando resultados"
          description={selectedProductCopy.loadingDescription}
          tone={isPokemonPilot ? 'default' : undefined}
        />
      )}

      {submittedQuery && !loading && error && (
        <StatePanel
          title="No pudimos cargar el catálogo"
          description={error}
          error
          tone={isPokemonPilot ? 'error' : undefined}
        />
      )}

      {submittedQuery && !loading && !error && items.length === 0 && (
        <StatePanel
          title="Sin resultados"
          description={selectedProductCopy.emptyDescription}
          tone={isPokemonPilot ? 'muted' : undefined}
        />
      )}

      {submittedQuery && !loading && !error && items.length > 0 && (
        <GameResultsGrid
          items={items}
          view={view}
          queryState={{ q: submittedQuery, type: productType }}
        />
      )}

      {collectionsLoading && (
        <StatePanel
          title="Cargando colecciones"
          description="Preparando los sets disponibles para este juego."
          tone="muted"
        />
      )}

      {!collectionsLoading && collectionsError && (
        <StatePanel
          title="No pudimos cargar las colecciones"
          description={collectionsError}
          error
          tone="error"
        />
      )}

      {!collectionsLoading && (
        <GameCollectionsList collections={collectionsData} gameSlug={game.slug} />
      )}

      {!newsLoading && newsError && (
        <StatePanel
          title="No pudimos cargar noticias oficiales"
          description={newsError}
          error
          tone="error"
        />
      )}

      <GameNewsGrid news={newsData} />
      <GameTournamentsByRegion tournaments={tournaments} />
    </section>
  )
}
