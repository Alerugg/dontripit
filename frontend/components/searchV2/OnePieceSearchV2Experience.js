'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import GameSearchBar from '../games/GameSearchBar'
import StatePanel from '../catalog/StatePanel'
import SectionHeader from '../ui/SectionHeader'
import AdvancedSearchPanel from './AdvancedSearchPanel'
import SearchV2Results from './SearchV2Results'
import { advancedSearchV2, fetchFacetsV2, searchV2, suggestV2 } from '../../lib/searchV2/client'
import { appendAdvancedFilters, hasFilterValues, readAdvancedFilters, safePage } from '../../lib/searchV2/urlState'
import './SearchV2.css'

const SEARCH_COPY_BY_GAME = {
  onepiece: {
    examples: ['Luffy', 'Zoro', 'OP05-119', 'Luffy OP05', 'red leader', 'monky lufi'],
    placeholder: 'Luffy, OP05-119, red leader, Luffy OP05 English SEC…',
    description: 'Nombre, número, set, idioma o una combinación natural. Los resultados normales agrupan variantes para no llenarte la pantalla de duplicados.',
    empty: 'Prueba otro nombre, collector number o combina carta + set.',
  },
  pokemon: {
    examples: ['Pikachu', 'Charizard', 'Pikachu 151', 'Fire Basic', 'SIR Holo', 'pikchu'],
    placeholder: 'Pikachu, Charizard, 151, Fire Basic, Special Illustration Rare…',
    description: 'Nombre, collector number, set o una combinación natural. La búsqueda normal agrupa la carta; Advanced Search baja a rareza, tipo, etapa, regulación y variante física.',
    empty: 'Prueba otro Pokémon, collector number, set o abre Advanced Search para filtrar la impresión exacta.',
  },
  yugioh: {
    examples: ['Dark Magician', 'Blue-Eyes White Dragon', '2017-EN001', 'DARK Monster', 'ATK 3000', 'Extra Secret Rare'],
    placeholder: 'Dark Magician, Blue-Eyes White Dragon, 2017-EN001, DARK Monster…',
    description: 'Nombre, código de impresión, release o una combinación natural. La búsqueda normal agrupa reimpresiones por carta; Advanced Search baja al Print exacto por rareza, clase, atributo, tipo y estadísticas.',
    empty: 'Prueba otro nombre, código de impresión, release o abre Advanced Search para combinar filtros Yu-Gi-Oh!.',
  },
}

const DEFAULT_SEARCH_COPY = {
  examples: [],
  placeholder: 'Nombre, número, set o combinación natural…',
  description: 'Busca por identidad y usa Advanced Search para bajar a la impresión física exacta.',
  empty: 'Prueba otro nombre, número o set.',
}

const ADVANCED_PAGE_SIZE = 24

function suggestionForLegacyRow(item) {
  return {
    ...item,
    primary_image_url: item.primary_image_url || item.image_url,
    set_name: item.set_name || item.set_code,
  }
}

export default function OnePieceSearchV2Experience({ game }) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const searchCopy = SEARCH_COPY_BY_GAME[game.slug] || DEFAULT_SEARCH_COPY
  const initialQuery = searchParams.get('q') || ''
  const initialAdvancedOpen = searchParams.get('advanced') === '1'
  const initialAdvancedFilters = readAdvancedFilters(searchParams)
  const initialAdvancedPage = safePage(searchParams.get('page'))
  const initialAdvancedRan = initialAdvancedOpen && (Boolean(initialQuery.trim()) || hasFilterValues(initialAdvancedFilters))
  const initialAdvancedHydrated = useRef(false)

  const [query, setQuery] = useState(initialQuery)
  const [submittedQuery, setSubmittedQuery] = useState(initialAdvancedRan ? '' : initialQuery)
  const [normalItems, setNormalItems] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const [error, setError] = useState('')

  const [facetGroups, setFacetGroups] = useState({})
  const [facetError, setFacetError] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(initialAdvancedOpen)
  const [advancedFilters, setAdvancedFilters] = useState(initialAdvancedFilters)
  const [appliedAdvancedFilters, setAppliedAdvancedFilters] = useState(initialAdvancedFilters)
  const [advancedQuery, setAdvancedQuery] = useState(initialAdvancedRan ? initialQuery : '')
  const [advancedItems, setAdvancedItems] = useState([])
  const [advancedTotal, setAdvancedTotal] = useState(0)
  const [advancedPage, setAdvancedPage] = useState(initialAdvancedPage)
  const [advancedLoading, setAdvancedLoading] = useState(false)
  const [advancedError, setAdvancedError] = useState('')
  const [advancedRan, setAdvancedRan] = useState(initialAdvancedRan)

  const hasActiveAdvancedFilters = useMemo(() => hasFilterValues(advancedFilters), [advancedFilters])
  const advancedPageCount = Math.max(1, Math.ceil(advancedTotal / ADVANCED_PAGE_SIZE))

  useEffect(() => {
    let cancelled = false
    async function loadFacets() {
      try {
        const payload = await fetchFacetsV2(game.slug)
        if (!cancelled) {
          setFacetGroups(payload?.groups || {})
          setFacetError('')
        }
      } catch (requestError) {
        if (!cancelled) setFacetError(requestError.message || 'No pudimos cargar los filtros avanzados.')
      }
    }
    loadFacets()
    return () => { cancelled = true }
  }, [game.slug])

  useEffect(() => {
    if (query.trim().length < 2) {
      setSuggestions([])
      return undefined
    }

    let cancelled = false
    const handle = setTimeout(async () => {
      setSuggestionsLoading(true)
      try {
        const rows = await suggestV2({ q: query.trim(), game: game.slug, limit: 8 })
        if (!cancelled) setSuggestions(rows.map(suggestionForLegacyRow))
      } catch {
        if (!cancelled) setSuggestions([])
      } finally {
        if (!cancelled) setSuggestionsLoading(false)
      }
    }, 160)

    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [game.slug, query])

  useEffect(() => {
    if (!submittedQuery.trim()) {
      setNormalItems([])
      setError('')
      return undefined
    }

    let cancelled = false
    async function runSearch() {
      setLoading(true)
      setError('')
      try {
        const rows = await searchV2({ q: submittedQuery.trim(), game: game.slug, limit: 48 })
        if (!cancelled) setNormalItems(rows)
      } catch (requestError) {
        if (!cancelled) {
          setNormalItems([])
          setError(requestError.message || 'No pudimos ejecutar Search V2.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    runSearch()
    return () => { cancelled = true }
  }, [game.slug, submittedQuery])

  useEffect(() => {
    if (initialAdvancedHydrated.current || !initialAdvancedRan) return
    initialAdvancedHydrated.current = true
    runAdvanced(initialAdvancedPage, { reuseApplied: true })
  }, [])

  useEffect(() => {
    const params = new URLSearchParams()
    const activeQuery = advancedRan ? advancedQuery.trim() : submittedQuery.trim()
    if (activeQuery) params.set('q', activeQuery)
    if (advancedOpen || advancedRan) params.set('advanced', '1')
    if (advancedRan) {
      appendAdvancedFilters(params, appliedAdvancedFilters)
      if (advancedPage > 1) params.set('page', String(advancedPage))
    }
    const next = params.toString()
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
  }, [advancedOpen, advancedPage, advancedQuery, advancedRan, appliedAdvancedFilters, pathname, router, submittedQuery])

  function submitNormal(nextQuery = query) {
    const clean = String(nextQuery || '').trim()
    setQuery(clean)
    setSubmittedQuery(clean)
    setAdvancedRan(false)
    setAdvancedItems([])
    setAdvancedTotal(0)
    setAdvancedPage(1)
    setAdvancedError('')
  }

  async function runAdvanced(nextPage = 1, { reuseApplied = false } = {}) {
    const page = Math.max(1, Number(nextPage) || 1)
    const filters = reuseApplied ? appliedAdvancedFilters : advancedFilters
    const searchQuery = reuseApplied ? advancedQuery : query.trim()

    if (!reuseApplied) {
      setAppliedAdvancedFilters(filters)
      setAdvancedQuery(searchQuery)
    }
    setSubmittedQuery('')
    setAdvancedPage(page)
    setAdvancedLoading(true)
    setAdvancedError('')
    setAdvancedRan(true)

    try {
      const payload = await advancedSearchV2({
        game: game.slug,
        q: searchQuery,
        filters,
        limit: ADVANCED_PAGE_SIZE,
        offset: (page - 1) * ADVANCED_PAGE_SIZE,
      })
      setAdvancedItems(payload?.items || [])
      setAdvancedTotal(payload?.total || 0)
    } catch (requestError) {
      setAdvancedItems([])
      setAdvancedTotal(0)
      setAdvancedError(requestError.message || 'No pudimos aplicar los filtros avanzados.')
    } finally {
      setAdvancedLoading(false)
    }
  }

  function updateAdvancedFilter(key, value) {
    setAdvancedFilters((current) => {
      const next = { ...current }
      if (value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)) delete next[key]
      else next[key] = value
      return next
    })
  }

  function resetAdvanced() {
    setAdvancedFilters({})
    setAppliedAdvancedFilters({})
    setAdvancedQuery('')
    setAdvancedItems([])
    setAdvancedTotal(0)
    setAdvancedPage(1)
    setAdvancedRan(false)
    setAdvancedError('')
  }

  return (
    <>
      <section className="game-section panel-soft sv2-search-shell" style={{ '--game-accent': game.accent }}>
        <SectionHeader
          compact
          eyebrow="Search V2"
          title="Busca como piensas, no como está organizada una base de datos."
          description={searchCopy.description}
        />

        <GameSearchBar
          value={query}
          onChange={setQuery}
          onSubmit={() => submitNormal(query)}
          suggestions={suggestions}
          suggestionsLoading={suggestionsLoading}
          onSuggestionSelect={(item) => {
            if (item.card_id) {
              router.push(`/games/${game.slug}/cards/${item.card_id}?q=${encodeURIComponent(item.name || '')}`)
              return
            }
            submitNormal(item.name || item.collector_number || '')
          }}
          placeholder={searchCopy.placeholder}
          variant="pilot"
        />

        {searchCopy.examples.length > 0 ? (
          <div className="sv2-example-row">
            <span>Prueba:</span>
            {searchCopy.examples.map((example) => (
              <button key={example} type="button" onClick={() => submitNormal(example)}>{example}</button>
            ))}
          </div>
        ) : null}

        {facetError ? <p className="sv2-inline-error">{facetError}</p> : null}

        <AdvancedSearchPanel
          gameSlug={game.slug}
          groups={facetGroups}
          values={advancedFilters}
          onChange={updateAdvancedFilter}
          onSearch={() => runAdvanced(1)}
          onReset={resetAdvanced}
          loading={advancedLoading}
          open={advancedOpen}
          onToggle={() => setAdvancedOpen((current) => !current)}
        />
      </section>

      {advancedRan ? (
        <>
          {advancedLoading ? <StatePanel title="Aplicando filtros" description="Buscando la impresión física exacta…" /> : null}
          {!advancedLoading && advancedError ? <StatePanel title="No pudimos filtrar" description={advancedError} error /> : null}
          {!advancedLoading && !advancedError && advancedItems.length === 0 ? (
            <StatePanel
              title="Sin prints para esta combinación"
              description={hasActiveAdvancedFilters ? 'Prueba quitando uno de los filtros activos.' : 'Añade al menos un filtro o una búsqueda.'}
              tone="muted"
            />
          ) : null}
          {!advancedLoading && !advancedError && advancedItems.length > 0 ? (
            <>
              <SearchV2Results items={advancedItems} mode="advanced" gameSlug={game.slug} query={advancedQuery} total={advancedTotal} />
              {advancedPageCount > 1 ? (
                <nav className="sv2-pagination" aria-label="Paginación de prints">
                  <button type="button" disabled={advancedPage <= 1 || advancedLoading} onClick={() => runAdvanced(advancedPage - 1, { reuseApplied: true })}>
                    ← Anterior
                  </button>
                  <span>Página <strong>{advancedPage}</strong> de {advancedPageCount} · {advancedTotal.toLocaleString()} prints</span>
                  <button type="button" disabled={advancedPage >= advancedPageCount || advancedLoading} onClick={() => runAdvanced(advancedPage + 1, { reuseApplied: true })}>
                    Siguiente →
                  </button>
                </nav>
              ) : null}
            </>
          ) : null}
        </>
      ) : (
        <>
          {submittedQuery && loading ? <StatePanel title="Buscando" description={`Buscando “${submittedQuery}” con Search V2…`} /> : null}
          {submittedQuery && !loading && error ? <StatePanel title="No pudimos buscar" description={error} error /> : null}
          {submittedQuery && !loading && !error && normalItems.length === 0 ? (
            <StatePanel title="Sin resultados" description={searchCopy.empty} tone="muted" />
          ) : null}
          {submittedQuery && !loading && !error && normalItems.length > 0 ? (
            <SearchV2Results items={normalItems} mode="normal" gameSlug={game.slug} query={submittedQuery} />
          ) : null}
        </>
      )}
    </>
  )
}
