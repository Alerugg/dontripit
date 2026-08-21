'use client'

import './GameSetPage.css'
import './GameSetPageV2.css'
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import ResultsGrid from '../catalog/ResultsGrid'
import StatePanel from '../catalog/StatePanel'
import { fetchSetDetail } from '../../lib/catalog/client'
import { getGameHref } from '../../lib/catalog/routes'
import { getGameConfig } from '../../lib/catalog/games'
import { getLocalSetImageCandidates } from '../../lib/catalog/setImages'

const PAGE_SIZE = 24

const BASE_SORT_OPTIONS = [
  { value: 'number_asc', label: 'Número ↑' },
  { value: 'number_desc', label: 'Número ↓' },
  { value: 'name_asc', label: 'Nombre A–Z' },
  { value: 'name_desc', label: 'Nombre Z–A' },
]

function sortOptions(kind) {
  if (kind === 'card') {
    return [
      ...BASE_SORT_OPTIONS,
      { value: 'coverage_desc', label: 'Cobertura exacta ↓' },
      { value: 'price_asc', label: 'Desde impresión exacta ↑' },
      { value: 'price_desc', label: 'Hasta impresión exacta ↓' },
    ]
  }
  return [
    ...BASE_SORT_OPTIONS,
    { value: 'price_asc', label: 'Precio exacto ↑' },
    { value: 'price_desc', label: 'Precio exacto ↓' },
  ]
}

function formatSetReleaseDate(value) {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return new Intl.DateTimeFormat('es', { year: 'numeric', month: 'long', day: '2-digit' }).format(parsed)
}

function pageWindow(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
  const pages = [1]
  const from = Math.max(2, current - 1)
  const to = Math.min(total - 1, current + 1)
  if (from > 2) pages.push('gap-left')
  for (let value = from; value <= to; value += 1) pages.push(value)
  if (to < total - 1) pages.push('gap-right')
  pages.push(total)
  return pages
}

function SetHeroVisual({ gameSlug, setCode, setName }) {
  const candidates = useMemo(() => getLocalSetImageCandidates(gameSlug, setCode), [gameSlug, setCode])
  const [candidateIndex, setCandidateIndex] = useState(0)

  useEffect(() => { setCandidateIndex(0) }, [gameSlug, setCode])
  const currentSrc = candidates[candidateIndex] || ''

  if (!currentSrc) return <div className="game-set-hero-placeholder"><span>{setCode || 'SET'}</span></div>

  return (
    <img
      src={currentSrc}
      alt={setName || setCode || 'Colección'}
      className="game-set-hero-image"
      onError={() => setCandidateIndex((current) => current + 1)}
    />
  )
}

function Stat({ label, value, hint }) {
  return (
    <div className="dri-set-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  )
}

function FilterControls({ facets, language, finish, rarity, pricedOnly, onLanguage, onFinish, onRarity, onPricedOnly, onClear, activeCount }) {
  return (
    <div className="dri-set-filter-stack">
      <label className="dri-set-filter-field">
        <span>Idioma de impresión</span>
        <select value={language} onChange={(event) => onLanguage(event.target.value)}>
          <option value="">Todos los idiomas</option>
          {(facets.languages || []).map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>

      <label className="dri-set-filter-field">
        <span>Acabado</span>
        <select value={finish} onChange={(event) => onFinish(event.target.value)}>
          <option value="">Todos los acabados</option>
          {(facets.finishes || []).map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>

      <label className="dri-set-filter-field">
        <span>Rareza</span>
        <select value={rarity} onChange={(event) => onRarity(event.target.value)}>
          <option value="">Todas las rarezas</option>
          {(facets.rarities || []).map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>

      <label className="dri-set-exact-toggle">
        <input type="checkbox" checked={pricedOnly} onChange={(event) => onPricedOnly(event.target.checked)} />
        <span>
          <strong>Solo con precio exacto</strong>
          <small>En Cartas: exige al menos una impresión coincidente con precio seguro.</small>
        </span>
      </label>

      {activeCount > 0 ? (
        <button type="button" className="dri-set-clear" onClick={onClear}>Limpiar filtros ({activeCount})</button>
      ) : null}
    </div>
  )
}

export default function GameSetPage({ gameSlug, setCode }) {
  const game = getGameConfig(gameSlug) || { slug: gameSlug, name: gameSlug }
  const [setDetail, setSetDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [kind, setKind] = useState('card')
  const [view, setView] = useState('grid')
  const [sort, setSort] = useState('number_asc')
  const [language, setLanguage] = useState('')
  const [finish, setFinish] = useState('')
  const [rarity, setRarity] = useState('')
  const [pricedOnly, setPricedOnly] = useState(false)

  useEffect(() => {
    let cancelled = false
    const handle = setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchSetDetail(gameSlug, setCode, {
          kind,
          q: query.trim(),
          sort,
          language,
          finish,
          rarity,
          pricedOnly,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        })
        if (!cancelled) setSetDetail(payload)
      } catch (requestError) {
        if (!cancelled) setError(requestError.message || 'No pudimos cargar la colección.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, query ? 180 : 0)

    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [finish, gameSlug, kind, language, page, pricedOnly, query, rarity, setCode, sort])

  function resetPage(action) {
    action()
    setPage(1)
  }

  function changeKind(nextKind) {
    setKind(nextKind)
    setPage(1)
    setSort('number_asc')
    if (nextKind === 'card') setView('grid')
  }

  function clearFilters() {
    setLanguage('')
    setFinish('')
    setRarity('')
    setPricedOnly(false)
    setPage(1)
  }

  if (loading && !setDetail) {
    return (
      <section className="page-shell game-page">
        <StatePanel title="Cargando colección" description="Organizando cartas canónicas e impresiones físicas." tone="default" />
      </section>
    )
  }

  if (error && !setDetail) {
    return (
      <section className="page-shell game-page">
        <StatePanel title="No pudimos cargar la colección" description={error} error tone="error" />
      </section>
    )
  }

  if (!setDetail?.set) {
    return (
      <section className="page-shell game-page">
        <StatePanel title="Colección no encontrada" description="No encontramos la colección solicitada." error tone="error" />
      </section>
    )
  }

  const collection = setDetail.set
  const items = setDetail.items || []
  const stats = setDetail.stats || {}
  const facets = setDetail.facets || { languages: [], finishes: [], rarities: [] }
  const resultTotal = Number(setDetail.total || 0)
  const totalPages = Math.max(1, Math.ceil(resultTotal / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const releaseLabel = formatSetReleaseDate(collection.release_date)
  const activeFilterCount = [language, finish, rarity].filter(Boolean).length + (pricedOnly ? 1 : 0)
  const displayedFrom = resultTotal ? ((safePage - 1) * PAGE_SIZE) + 1 : 0
  const displayedTo = Math.min(safePage * PAGE_SIZE, resultTotal)
  const coverage = Number(stats.price_coverage || 0)
  const currentSortOptions = sortOptions(kind)

  return (
    <section className="page-shell game-page game-set-page">
      <header className="panel game-set-hero">
        <div className="game-set-hero-copy">
          <nav className="game-set-breadcrumbs" aria-label="Breadcrumb">
            <Link href={getGameHref(game.slug)}>{game.name}</Link>
            <span>/</span>
            <Link href={`/games/${game.slug}/sets`}>Sets</Link>
            <span>/</span>
            <span aria-current="page">{collection.code}</span>
          </nav>

          <p className="eyebrow">Set</p>
          <h1>{collection.name}</h1>
          <p className="game-set-hero-meta-line">
            {String(collection.code || '').toUpperCase()} · {Number(stats.cards || 0).toLocaleString()} cartas · {Number(stats.prints || 0).toLocaleString()} impresiones
          </p>

          <div className="game-set-meta-chips">
            {releaseLabel ? <span>Lanzamiento: {releaseLabel}</span> : null}
            {collection.series ? <span>Serie: {collection.series}</span> : null}
            {(stats.languages || []).length ? <span>Idiomas: {stats.languages.join(' · ')}</span> : null}
          </div>

          <p>Empieza por la carta canónica. Cuando necesites idioma, acabado, variante o precio, baja a la impresión física exacta.</p>

          <div className="toolbar-row">
            <Link href={getGameHref(game.slug)} className="secondary-btn">Volver a {game.name}</Link>
            <Link href={`/games/${game.slug}/sets`} className="secondary-btn">Todos los sets</Link>
          </div>
        </div>

        <div className="game-set-hero-visual">
          <div className="game-set-hero-art">
            <div className="game-set-hero-media">
              <SetHeroVisual gameSlug={game.slug} setCode={collection.code} setName={collection.name} />
            </div>
            <div className="game-set-hero-art-copy">
              <strong>{collection.name}</strong>
              <small>{String(collection.code || '').toUpperCase()}</small>
            </div>
          </div>
        </div>
      </header>

      <section className="dri-set-stats" aria-label="Resumen del set">
        <Stat label="Cartas" value={Number(stats.cards || 0).toLocaleString()} hint="Identidades canónicas" />
        <Stat label="Impresiones" value={Number(stats.prints || 0).toLocaleString()} hint="Objetos físicos" />
        <Stat label="Con precio exacto" value={`${Number(stats.priced_prints || 0).toLocaleString()}/${Number(stats.prints || 0).toLocaleString()}`} hint="Mapeos seguros" />
        <Stat label="Cobertura exacta" value={`${coverage}%`} hint="Sin estimar las demás" />
      </section>

      <section className="game-section dri-set-catalog">
        <div className="dri-set-catalog-head">
          <div className="section-heading compact">
            <p className="eyebrow">Catálogo del set</p>
            <h2>{kind === 'card' ? 'Cartas canónicas' : 'Impresiones físicas'}</h2>
            <p>
              {kind === 'card'
                ? 'Una carta por identidad. Abre una para elegir después su versión física exacta.'
                : 'Cada resultado es una impresión concreta: idioma, acabado, variante y precio pertenecen a esta capa.'}
            </p>
          </div>

          <label className="dri-set-search">
            <span>Buscar dentro del set</span>
            <input value={query} onChange={(event) => resetPage(() => setQuery(event.target.value))} placeholder="Nombre, número o rareza…" />
          </label>
        </div>

        <div className="dri-set-tabs" role="tablist" aria-label="Tipo de objeto del set">
          <button type="button" role="tab" aria-selected={kind === 'card'} className={kind === 'card' ? 'is-active' : ''} onClick={() => changeKind('card')}>
            Cartas <span>{Number(stats.cards || 0).toLocaleString()}</span>
          </button>
          <button type="button" role="tab" aria-selected={kind === 'print'} className={kind === 'print' ? 'is-active' : ''} onClick={() => changeKind('print')}>
            Impresiones <span>{Number(stats.prints || 0).toLocaleString()}</span>
          </button>
        </div>

        <details className="dri-set-mobile-filters">
          <summary>Filtros{activeFilterCount ? ` (${activeFilterCount})` : ''}</summary>
          <FilterControls
            facets={facets}
            language={language}
            finish={finish}
            rarity={rarity}
            pricedOnly={pricedOnly}
            onLanguage={(value) => resetPage(() => setLanguage(value))}
            onFinish={(value) => resetPage(() => setFinish(value))}
            onRarity={(value) => resetPage(() => setRarity(value))}
            onPricedOnly={(value) => resetPage(() => setPricedOnly(value))}
            onClear={clearFilters}
            activeCount={activeFilterCount}
          />
        </details>

        <div className="dri-set-workspace">
          <aside className="dri-set-sidebar" aria-label="Filtros del set">
            <p className="dri-set-sidebar-title">Filtros</p>
            <FilterControls
              facets={facets}
              language={language}
              finish={finish}
              rarity={rarity}
              pricedOnly={pricedOnly}
              onLanguage={(value) => resetPage(() => setLanguage(value))}
              onFinish={(value) => resetPage(() => setFinish(value))}
              onRarity={(value) => resetPage(() => setRarity(value))}
              onPricedOnly={(value) => resetPage(() => setPricedOnly(value))}
              onClear={clearFilters}
              activeCount={activeFilterCount}
            />
          </aside>

          <div className="dri-set-results">
            <div className="dri-set-results-toolbar">
              <p aria-live="polite">
                {loading ? 'Actualizando…' : resultTotal
                  ? <>Mostrando <strong>{displayedFrom}–{displayedTo}</strong> de <strong>{resultTotal.toLocaleString()}</strong></>
                  : 'Sin resultados'}
                {query ? <> · “{query}”</> : null}
              </p>

              <div className="dri-set-results-actions">
                <label className="dri-set-sort">
                  <span className="sr-only">Ordenar resultados</span>
                  <select value={sort} onChange={(event) => resetPage(() => setSort(event.target.value))}>
                    {currentSortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                <div className="dri-set-view" aria-label="Vista de resultados">
                  <button type="button" className={view === 'grid' ? 'is-active' : ''} aria-pressed={view === 'grid'} onClick={() => setView('grid')}>Grid</button>
                  <button type="button" className={view === 'list' ? 'is-active' : ''} aria-pressed={view === 'list'} onClick={() => setView('list')}>Lista</button>
                </div>
              </div>
            </div>

            {setDetail.truncated ? (
              <div className="dri-set-integrity-note" role="status">Este set supera el límite de seguridad de carga. Los conteos visibles corresponden únicamente a las impresiones recuperadas y no se extrapolan.</div>
            ) : null}
            {error ? <div className="dri-set-integrity-note is-error" role="alert">{error}</div> : null}

            {!items.length && !loading ? (
              <StatePanel
                title="No encontramos coincidencias"
                description="Prueba otro término o limpia los filtros físicos. No ocultamos resultados inventando equivalencias entre impresiones."
                tone="muted"
              />
            ) : (
              <div className={loading ? 'dri-set-results-loading' : ''}>
                <ResultsGrid items={items} view={view} queryState={{ set_code: collection.code }} />
              </div>
            )}

            {totalPages > 1 ? (
              <nav className="dri-set-pagination" aria-label="Paginación del set">
                <button type="button" disabled={safePage <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}>← Anterior</button>
                <div className="dri-set-page-numbers">
                  {pageWindow(safePage, totalPages).map((value) => typeof value === 'string'
                    ? <span key={value} aria-hidden="true">…</span>
                    : (
                      <button
                        key={value}
                        type="button"
                        className={safePage === value ? 'is-active' : ''}
                        aria-current={safePage === value ? 'page' : undefined}
                        aria-label={`Página ${value}`}
                        disabled={loading}
                        onClick={() => setPage(value)}
                      >{value}</button>
                    ))}
                </div>
                <button type="button" disabled={safePage >= totalPages || loading} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Siguiente →</button>
              </nav>
            ) : null}
          </div>
        </div>

        <div className="dri-set-integrity-footer">
          <span>Carta → Impresión → Mercado</span>
          <p>La cobertura se calcula únicamente con impresiones que tienen precio exacto verificable. Una carta canónica nunca recibe un precio universal.</p>
        </div>
      </section>
    </section>
  )
}