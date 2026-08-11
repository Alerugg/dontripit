'use client'

import './GameSetPage.css'
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import ResultsGrid from '../catalog/ResultsGrid'
import StatePanel from '../catalog/StatePanel'
import { fetchSetDetail } from '../../lib/catalog/client'
import { getGameHref } from '../../lib/catalog/routes'
import { getGameConfig } from '../../lib/catalog/games'
import { getLocalSetImageCandidates } from '../../lib/catalog/setImages'

const PAGE_SIZE = 36

function formatSetReleaseDate(value) {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return new Intl.DateTimeFormat('es', { year: 'numeric', month: 'long', day: '2-digit' }).format(parsed)
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

export default function GameSetPage({ gameSlug, setCode }) {
  const game = getGameConfig(gameSlug) || { slug: gameSlug, name: gameSlug }
  const [setDetail, setSetDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('number_asc')

  useEffect(() => {
    let cancelled = false
    const handle = setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchSetDetail(gameSlug, setCode, {
          q: query.trim(),
          sort,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        })
        if (!cancelled) setSetDetail(payload)
      } catch (requestError) {
        if (!cancelled) {
          setSetDetail(null)
          setError(requestError.message || 'No pudimos cargar la colección.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, query ? 180 : 0)

    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [gameSlug, page, query, setCode, sort])

  function changeQuery(value) {
    setQuery(value)
    setPage(1)
  }

  function changeSort(value) {
    setSort(value)
    setPage(1)
  }

  if (loading && !setDetail) {
    return (
      <section className="page-shell game-page">
        <StatePanel title="Cargando colección" description="Preparando esta página de versiones físicas." tone="default" />
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
  const cards = setDetail.cards || []
  const resultTotal = Number(setDetail.total || 0)
  const unfilteredTotal = Number(collection.collector_total || collection.print_count || resultTotal)
  const totalPages = Math.max(1, Math.ceil(resultTotal / PAGE_SIZE))
  const releaseLabel = formatSetReleaseDate(collection.release_date)
  const totalCards = Number(collection.card_count || 0)

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
          <p className="game-set-hero-meta-line">{String(collection.code || '').toUpperCase()} · {unfilteredTotal.toLocaleString()} versiones físicas</p>

          <div className="game-set-meta-chips">
            {releaseLabel ? <span>Lanzamiento: {releaseLabel}</span> : null}
            {totalCards > 0 ? <span>{totalCards} cartas distintas</span> : null}
            {collection.series ? <span>Serie: {collection.series}</span> : null}
          </div>

          <p>Checklist completo del set. Cada alternativa, promo, idioma o reimpresión con identidad propia abre directamente su ficha exacta.</p>

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

      <section className="game-section dri-set-checklist">
        <div className="dri-set-checklist-head">
          <div className="section-heading compact">
            <p className="eyebrow">Checklist</p>
            <h2>Todas las versiones de esta colección</h2>
            <p>{resultTotal.toLocaleString()} versión{resultTotal === 1 ? '' : 'es'} · página {page} de {totalPages}.</p>
          </div>
          <div className="dri-set-controls">
            <label className="dri-set-search">
              <span>Buscar dentro del set</span>
              <input value={query} onChange={(event) => changeQuery(event.target.value)} placeholder="Nombre, número, rareza…" />
            </label>
            <label className="dri-set-sort">
              <span>Ordenar</span>
              <select value={sort} onChange={(event) => changeSort(event.target.value)}>
                <option value="number_asc">Número ↑</option>
                <option value="number_desc">Número ↓</option>
                <option value="name_asc">Nombre A–Z</option>
                <option value="name_desc">Nombre Z–A</option>
              </select>
            </label>
          </div>
        </div>

        {loading ? <p className="dri-set-filter-count">Actualizando página…</p> : null}
        {query ? <p className="dri-set-filter-count">{resultTotal.toLocaleString()} coincidencia{resultTotal === 1 ? '' : 's'} para “{query}”.</p> : null}
        {error ? <p className="dri-set-filter-count">{error}</p> : null}

        {!cards.length && !loading ? (
          <StatePanel
            title={unfilteredTotal ? 'No encontramos esa carta en el set' : 'Sin versiones cargadas todavía'}
            description={unfilteredTotal ? 'Prueba con otro nombre, número o rareza.' : 'La colección existe, pero aún no tenemos versiones asociadas en esta vista.'}
            tone="muted"
          />
        ) : (
          <ResultsGrid items={cards} view="grid" queryState={{ type: 'singles', set_code: collection.code }} />
        )}

        {totalPages > 1 ? (
          <nav className="dri-set-pagination" aria-label="Paginación de versiones del set">
            <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}>← Anterior</button>
            <span>Página <strong>{page}</strong> de {totalPages} · {resultTotal.toLocaleString()} versiones</span>
            <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Siguiente →</button>
          </nav>
        ) : null}
      </section>
    </section>
  )
}
