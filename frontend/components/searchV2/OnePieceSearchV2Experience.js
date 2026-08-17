'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import GameSearchBar from '../games/GameSearchBar'
import StatePanel from '../catalog/StatePanel'
import SectionHeader from '../ui/SectionHeader'
import AdvancedSearchPanel from './AdvancedSearchPanel'
import FederatedSearchResults from './FederatedSearchResults'
import SearchV2Results from './SearchV2Results'
import { advancedSearchV2, federatedSearchV2, fetchFacetsV2, suggestV2 } from '../../lib/searchV2/client'
import { appendAdvancedFilters, hasFilterValues, readAdvancedFilters, safePage } from '../../lib/searchV2/urlState'
import './SearchV2.css'

const SEARCH_COPY_BY_GAME = {
  onepiece: {
    examples: ['Luffy', 'OP15', 'OP05-119'],
    placeholder: 'Luffy, OP15, OP05-119…',
    description: 'Busca carta, número o colección. Si escribes un set como OP15 verás su checklist, sellado y coincidencias en una sola búsqueda.',
    empty: 'Prueba otro nombre, número de carta o set.',
  },
  pokemon: {
    examples: ['Pikachu', 'Charizard', '151'],
    placeholder: 'Pikachu, Charizard, 151…',
    description: 'Busca por nombre, número o set. Las versiones físicas se paginan para mantener la búsqueda rápida.',
    empty: 'Prueba otro Pokémon, número o set.',
  },
  yugioh: {
    examples: ['Dark Magician', 'Mago Oscuro', 'ブラック・マジシャン'],
    placeholder: 'Dark Magician, Mago Oscuro, ブラック・マジシャン…',
    description: 'Busca por nombre en inglés, español o japonés, o por código. El idioma selecciona versiones físicas localizadas sin mezclar su identidad ni el precio exacto.',
    empty: 'Prueba otro nombre localizado, código o set.',
  },
  magic: {
    examples: ['Black Lotus', 'Sol Ring', 'Lightning Bolt'],
    placeholder: 'Black Lotus, Sol Ring, Lightning Bolt…',
    description: 'Busca primero la carta. Las versiones físicas y productos relacionados se cargan por páginas.',
    empty: 'Prueba otro nombre o set.',
  },
}

const DEFAULT_SEARCH_COPY = {
  examples: [],
  placeholder: 'Nombre, número o set…',
  description: 'Busca primero la carta y afina solo si necesitas una versión física concreta.',
  empty: 'Prueba otro nombre, número o set.',
}

const ADVANCED_PAGE_SIZE = 24
const NORMAL_PAGE_SIZE = 24
const RESULT_SORTS = new Set(['relevance', 'price_desc', 'price_asc', 'number_asc', 'number_desc', 'name_asc', 'name_desc'])
const YUGIOH_LANGUAGE_CODES = ['en', 'es', 'ja']
const YUGIOH_LANGUAGES = new Set(YUGIOH_LANGUAGE_CODES)
const YUGIOH_LANGUAGE_LABELS = {
  en: 'English',
  es: 'Español',
  ja: '日本語',
}

function initialSort(searchParams) {
  const value = searchParams.get('sort') || 'relevance'
  return RESULT_SORTS.has(value) ? value : 'relevance'
}

function initialSearchLanguages(searchParams, gameSlug) {
  if (gameSlug !== 'yugioh') return []
  const raw = String(searchParams.get('lang') || '').trim().toLowerCase()
  if (!raw || raw === 'all') return []
  const values = raw
    .split(',')
    .map((value) => value.trim())
    .filter((value) => YUGIOH_LANGUAGES.has(value))
  return [...new Set(values)]
}

function visibleFacetGroups(groups, gameSlug) {
  if (gameSlug !== 'yugioh') return groups
  return Object.fromEntries(
    Object.entries(groups || {})
      .map(([group, facets]) => [group, (facets || []).filter((facet) => facet.key !== 'language')])
      .filter(([, facets]) => facets.length > 0),
  )
}

function suggestionForLegacyRow(item) {
  return {
    ...item,
    primary_image_url: item.primary_image_url || item.image_url,
    set_name: item.set_name || item.set_code,
  }
}

function payloadCount(payload) {
  const counts = payload?.counts || {}
  return Number(counts.singles || 0) + Number(counts.sets || 0) + Number(counts.sealed || 0) + Number(counts.matches || 0)
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
  const initialLanguages = initialSearchLanguages(searchParams, game.slug)
  const initialAdvancedRan = initialAdvancedOpen && (
    Boolean(initialQuery.trim())
    || hasFilterValues(initialAdvancedFilters)
    || (game.slug === 'yugioh' && initialLanguages.length > 0)
  )
  const initialAdvancedHydrated = useRef(false)

  const [query, setQuery] = useState(initialQuery)
  const [submittedQuery, setSubmittedQuery] = useState(initialAdvancedRan ? '' : initialQuery)
  const [normalPayload, setNormalPayload] = useState(null)
  const [normalPage, setNormalPage] = useState(safePage(searchParams.get('search_page')))
  const [normalType, setNormalType] = useState(() => {
    const value = searchParams.get('kind') || 'all'
    return ['all', 'singles', 'sets', 'sealed', 'matches'].includes(value) ? value : 'all'
  })
  const [normalCategory, setNormalCategory] = useState(searchParams.get('category') || '')
  const [resultSort, setResultSort] = useState(initialSort(searchParams))
  const [onlyWithPrice, setOnlyWithPrice] = useState(searchParams.get('priced') === '1')
  const [searchLanguages, setSearchLanguages] = useState(initialLanguages)
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

  const activeLanguage = game.slug === 'yugioh' ? searchLanguages.join(',') : ''
  const hasActiveAdvancedFilters = useMemo(
    () => hasFilterValues(advancedFilters) || Boolean(activeLanguage),
    [activeLanguage, advancedFilters],
  )
  const advancedPageCount = Math.max(1, Math.ceil(advancedTotal / ADVANCED_PAGE_SIZE))

  useEffect(() => {
    let cancelled = false
    async function loadFacets() {
      try {
        const payload = await fetchFacetsV2(game.slug)
        if (!cancelled) {
          setFacetGroups(visibleFacetGroups(payload?.groups || {}, game.slug))
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
    if (query.trim().length < 1) {
      setSuggestions([])
      return undefined
    }

    let cancelled = false
    const handle = setTimeout(async () => {
      setSuggestionsLoading(true)
      try {
        const rows = await suggestV2({ q: query.trim(), game: game.slug, limit: 8, language: activeLanguage })
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
  }, [activeLanguage, game.slug, query])

  useEffect(() => {
    if (!submittedQuery.trim()) {
      setNormalPayload(null)
      setError('')
      return undefined
    }

    let cancelled = false
    async function runSearch() {
      setLoading(true)
      setError('')
      try {
        const payload = await federatedSearchV2({
          q: submittedQuery.trim(),
          game: game.slug,
          page: normalPage,
          limit: NORMAL_PAGE_SIZE,
          kind: normalType,
          category: normalCategory,
          sort: resultSort,
          hasPrice: onlyWithPrice,
          language: activeLanguage,
        })
        if (!cancelled) setNormalPayload(payload)
      } catch (requestError) {
        if (!cancelled) {
          setNormalPayload(null)
          setError(requestError.message || 'No pudimos ejecutar la búsqueda.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    runSearch()
    return () => { cancelled = true }
  }, [activeLanguage, game.slug, normalCategory, normalPage, normalType, onlyWithPrice, resultSort, submittedQuery])

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
    } else {
      if (normalPage > 1) params.set('search_page', String(normalPage))
      if (normalType !== 'all') params.set('kind', normalType)
      if (normalCategory) params.set('category', normalCategory)
    }
    if (resultSort !== 'relevance') params.set('sort', resultSort)
    if (onlyWithPrice) params.set('priced', '1')
    if (game.slug === 'yugioh' && activeLanguage) params.set('lang', activeLanguage)
    const next = params.toString()
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
  }, [activeLanguage, advancedOpen, advancedPage, advancedQuery, advancedRan, appliedAdvancedFilters, game.slug, normalCategory, normalPage, normalType, onlyWithPrice, pathname, resultSort, router, submittedQuery])

  function submitNormal(nextQuery = query) {
    const clean = String(nextQuery || '').trim()
    setQuery(clean)
    setSubmittedQuery(clean)
    setNormalPage(1)
    setNormalType('all')
    setNormalCategory('')
    setAdvancedRan(false)
    setAdvancedItems([])
    setAdvancedTotal(0)
    setAdvancedPage(1)
    setAdvancedError('')
  }

  function goToNormalPage(page) {
    setNormalPage(Math.max(1, Number(page) || 1))
    if (typeof document !== 'undefined') {
      document.getElementById('buscar')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  function changeNormalType(type) {
    setNormalType(type)
    setNormalPage(1)
  }

  function changeNormalCategory(category) {
    setNormalCategory(category)
    setNormalType('sealed')
    setNormalPage(1)
  }

  async function runAdvanced(nextPage = 1, { reuseApplied = false, sortOverride = null, hasPriceOverride = null, languageOverride = null } = {}) {
    const page = Math.max(1, Number(nextPage) || 1)
    const filters = reuseApplied ? appliedAdvancedFilters : advancedFilters
    const searchQuery = reuseApplied ? advancedQuery : query.trim()
    const activeSort = sortOverride ?? resultSort
    const activeHasPrice = hasPriceOverride ?? onlyWithPrice
    const activeDisplayLanguage = languageOverride ?? activeLanguage

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
        sort: activeSort,
        hasPrice: activeHasPrice,
        language: activeDisplayLanguage,
        limit: ADVANCED_PAGE_SIZE,
        offset: (page - 1) * ADVANCED_PAGE_SIZE,
      })
      setAdvancedItems(payload?.items || [])
      setAdvancedTotal(payload?.total || 0)
    } catch (requestError) {
      setAdvancedItems([])
      setAdvancedTotal(0)
      setAdvancedError(requestError.message || 'No pudimos aplicar los filtros.')
    } finally {
      setAdvancedLoading(false)
    }
  }

  function changeResultSort(value) {
    setResultSort(value)
    setNormalPage(1)
    if (advancedRan) runAdvanced(1, { reuseApplied: true, sortOverride: value })
  }

  function changeOnlyWithPrice(value) {
    setOnlyWithPrice(value)
    setNormalPage(1)
    if (advancedRan) runAdvanced(1, { reuseApplied: true, hasPriceOverride: value })
  }

  function changeSearchLanguage(value) {
    let nextLanguages = []
    if (value !== 'all' && YUGIOH_LANGUAGES.has(value)) {
      nextLanguages = searchLanguages.includes(value)
        ? searchLanguages.filter((language) => language !== value)
        : [...searchLanguages, value]
    }
    setSearchLanguages(nextLanguages)
    setNormalPage(1)
    if (advancedRan) {
      runAdvanced(1, {
        reuseApplied: true,
        languageOverride: nextLanguages.join(','),
      })
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
          eyebrow="Buscar"
          title="Encuentra cartas, colecciones y sellado"
          description={searchCopy.description}
        />

        <GameSearchBar
          value={query}
          onChange={setQuery}
          onSubmit={() => submitNormal(query)}
          suggestions={suggestions}
          suggestionsLoading={suggestionsLoading}
          onSuggestionSelect={(item) => {
            const exactPrintId = item.print_id || (item.type === 'print' ? item.id : null)
            if (exactPrintId) {
              router.push(`/prints/${exactPrintId}`)
              return
            }
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
            <span>Ejemplos:</span>
            {searchCopy.examples.map((example) => (
              <button key={example} type="button" onClick={() => submitNormal(example)}>{example}</button>
            ))}
          </div>
        ) : null}

        <div className="sv2-global-controls">
          {game.slug === 'yugioh' ? (
            <div className="sv2-language-control">
              <span>Idioma de la carta</span>
              <div className="sv2-chip-list" role="group" aria-label="Filtrar Yu-Gi-Oh por idioma">
                <button
                  type="button"
                  className={`sv2-chip ${searchLanguages.length === 0 ? 'is-active' : ''}`}
                  aria-pressed={searchLanguages.length === 0}
                  onClick={() => changeSearchLanguage('all')}
                >
                  Todos
                </button>
                {YUGIOH_LANGUAGE_CODES.map((language) => (
                  <button
                    key={language}
                    type="button"
                    className={`sv2-chip ${searchLanguages.includes(language) ? 'is-active' : ''}`}
                    aria-pressed={searchLanguages.includes(language)}
                    onClick={() => changeSearchLanguage(language)}
                  >
                    {YUGIOH_LANGUAGE_LABELS[language]}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          <label>
            <span>Ordenar versiones</span>
            <select value={resultSort} onChange={(event) => changeResultSort(event.target.value)}>
              <option value="relevance">Relevancia / catálogo</option>
              <option value="price_desc">Precio: mayor a menor</option>
              <option value="price_asc">Precio: menor a mayor</option>
              <option value="number_asc">Número: menor a mayor</option>
              <option value="number_desc">Número: mayor a menor</option>
              <option value="name_asc">Nombre A–Z</option>
              <option value="name_desc">Nombre Z–A</option>
            </select>
          </label>
          <label className="sv2-price-toggle">
            <input type="checkbox" checked={onlyWithPrice} onChange={(event) => changeOnlyWithPrice(event.target.checked)} />
            <span>Solo con precio Cardmarket exacto y actual</span>
          </label>
        </div>

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
          {advancedLoading ? <StatePanel title="Aplicando filtros" description="Buscando versiones que coincidan…" loading /> : null}
          {!advancedLoading && advancedError ? <StatePanel title="No pudimos filtrar" description={advancedError} error /> : null}
          {!advancedLoading && !advancedError && advancedItems.length === 0 ? (
            <StatePanel
              title="No encontramos esa combinación"
              description={hasActiveAdvancedFilters ? 'Prueba quitando uno de los filtros.' : 'Añade un filtro o una búsqueda.'}
              tone="muted"
            />
          ) : null}
          {!advancedLoading && !advancedError && advancedItems.length > 0 ? (
            <>
              <SearchV2Results items={advancedItems} mode="advanced" gameSlug={game.slug} query={advancedQuery} total={advancedTotal} />
              {advancedPageCount > 1 ? (
                <nav className="sv2-pagination" aria-label="Paginación de versiones">
                  <button type="button" disabled={advancedPage <= 1 || advancedLoading} onClick={() => runAdvanced(advancedPage - 1, { reuseApplied: true })}>
                    ← Anterior
                  </button>
                  <span>Página <strong>{advancedPage}</strong> de {advancedPageCount} · {advancedTotal.toLocaleString()} versiones</span>
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
          {submittedQuery && loading ? <StatePanel title="Buscando en el catálogo" description={`Cargando cartas, colección y sellado para “${submittedQuery}”…`} loading /> : null}
          {submittedQuery && !loading && error ? <StatePanel title="No pudimos buscar" description={error} error /> : null}
          {submittedQuery && !loading && !error && payloadCount(normalPayload) === 0 ? (
            <StatePanel title="Sin resultados" description={searchCopy.empty} tone="muted" />
          ) : null}
          {submittedQuery && !loading && !error && payloadCount(normalPayload) > 0 ? (
            <FederatedSearchResults
              payload={normalPayload}
              gameSlug={game.slug}
              query={submittedQuery}
              activeType={normalType}
              onTypeChange={changeNormalType}
              page={normalPage}
              onPageChange={goToNormalPage}
              pageSize={NORMAL_PAGE_SIZE}
              category={normalCategory}
              onCategoryChange={changeNormalCategory}
            />
          ) : null}
        </>
      )}
    </>
  )
}
