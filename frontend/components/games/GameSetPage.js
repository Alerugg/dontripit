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

function extractCollectorNumber(value = '') {
  const raw = String(value || '')
  const trailing = raw.match(/(\d+)$/)
  if (trailing) return Number(trailing[1])

  const first = raw.match(/\d+/)
  return first ? Number(first[0]) : Number.MAX_SAFE_INTEGER
}

function sortCardsByCollectorNumber(cards = []) {
  return [...cards].sort((a, b) => {
    const aCollector = a.collector_number || ''
    const bCollector = b.collector_number || ''

    const aNum = extractCollectorNumber(aCollector)
    const bNum = extractCollectorNumber(bCollector)

    if (aNum !== bNum) return aNum - bNum

    const rawCompare = String(aCollector).localeCompare(String(bCollector), undefined, {
      numeric: true,
      sensitivity: 'base',
    })

    if (rawCompare !== 0) return rawCompare

    return String(a.name || a.title || '').localeCompare(
      String(b.name || b.title || ''),
      undefined,
      { sensitivity: 'base' },
    )
  })
}

function formatSetReleaseDate(value) {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''

  return new Intl.DateTimeFormat('es', {
    year: 'numeric',
    month: 'long',
    day: '2-digit',
  }).format(parsed)
}

function SetHeroVisual({ gameSlug, setCode, setName }) {
  const candidates = useMemo(
    () => getLocalSetImageCandidates(gameSlug, setCode),
    [gameSlug, setCode],
  )

  const [candidateIndex, setCandidateIndex] = useState(0)

  useEffect(() => {
    setCandidateIndex(0)
  }, [gameSlug, setCode])

  const currentSrc = candidates[candidateIndex] || ''

  if (!currentSrc) {
    return (
      <div className="game-set-hero-placeholder">
        <span>{setCode || 'SET'}</span>
      </div>
    )
  }

  return (
    <img
      src={currentSrc}
      alt={setName || setCode || 'Colección'}
      className="game-set-hero-image"
      onError={() => {
        setCandidateIndex((current) => current + 1)
      }}
    />
  )
}

export default function GameSetPage({ gameSlug, setCode }) {
  const game = getGameConfig(gameSlug) || { slug: gameSlug, name: gameSlug }
  const [setDetail, setSetDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadSet() {
      setLoading(true)
      setError('')

      try {
        const payload = await fetchSetDetail(gameSlug, setCode, { limit: 1000 })
        if (!cancelled) setSetDetail(payload)
      } catch (requestError) {
        if (!cancelled) {
          setSetDetail(null)
          setError(requestError.message || 'No pudimos cargar la colección.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadSet()

    return () => {
      cancelled = true
    }
  }, [gameSlug, setCode])

  const orderedCards = useMemo(() => {
    const sorted = sortCardsByCollectorNumber(setDetail?.cards || [])
    const collectorTotal = Number(setDetail?.set?.collector_total || 0)

    return sorted.map((item) => ({
      ...item,
      collector_total: collectorTotal,
    }))
  }, [setDetail])

  if (loading) {
    return (
      <section className="page-shell game-page">
        <StatePanel
          title="Cargando colección"
          description="Estamos preparando la colección y ordenando sus cartas."
          tone="default"
        />
      </section>
    )
  }

  if (error) {
    return (
      <section className="page-shell game-page">
        <StatePanel
          title="No pudimos cargar la colección"
          description={error}
          error
          tone="error"
        />
      </section>
    )
  }

  if (!setDetail?.set) {
    return (
      <section className="page-shell game-page">
        <StatePanel
          title="Colección no encontrada"
          description="No encontramos la colección solicitada."
          error
          tone="error"
        />
      </section>
    )
  }

  const collection = setDetail.set
  const releaseLabel = formatSetReleaseDate(collection.release_date)
  const totalCards = Number(collection.card_count || 0)

  return (
    <section className="page-shell game-page game-set-page">
      <header className="panel game-set-hero">
        <div className="game-set-hero-copy">
          <nav className="game-set-breadcrumbs" aria-label="Breadcrumb">
            <Link href={getGameHref(game.slug)}>Hub</Link>
            <span>/</span>
            <Link href={`/games/${game.slug}/sets`}>Sets</Link>
            <span>/</span>
            <span aria-current="page">{collection.code}</span>
          </nav>

          <p className="eyebrow">Colección</p>
          <h1>{collection.name}</h1>
          <p className="game-set-hero-meta-line">
            {collection.code} · {collection.print_count || orderedCards.length} prints
          </p>

          <div className="game-set-meta-chips">
            {releaseLabel ? <span>Lanzamiento: {releaseLabel}</span> : null}
            {totalCards > 0 ? <span>Cartas del set: {totalCards}</span> : null}
            {collection.series ? <span>Serie: {collection.series}</span> : null}
          </div>

          <p>
            Explora esta colección print por print. Esta vista ya no agrupa por carta maestra:
            muestra el checklist real del set en orden de colección.
          </p>

          <div className="toolbar-row">
            <Link href={getGameHref(game.slug)} className="secondary-btn">
              Volver al hub
            </Link>
            <Link href={`/games/${game.slug}/sets`} className="secondary-btn">
              Ver todos los sets
            </Link>
          </div>
        </div>

        <div className="game-set-hero-visual">
          <div className="game-set-hero-art">
            <div className="game-set-hero-media">
              <SetHeroVisual
                gameSlug={game.slug}
                setCode={collection.code}
                setName={collection.name}
              />
            </div>

            <div className="game-set-hero-art-copy">
              <strong>{collection.name}</strong>
              <small>{collection.code}</small>
            </div>
          </div>
        </div>
      </header>

      <section className="game-section">
        <div className="section-heading compact">
          <p className="eyebrow">Checklist</p>
          <h2>Prints de esta colección</h2>
          <p>
            {orderedCards.length} print{orderedCards.length === 1 ? '' : 's'} dentro de {collection.name}.
          </p>
        </div>

        {!orderedCards.length ? (
          <StatePanel
            title="Sin prints cargados todavía"
            description="La colección existe, pero aún no hemos cargado prints en esta vista."
            tone="muted"
          />
        ) : (
          <ResultsGrid
            items={orderedCards}
            view="grid"
            queryState={{
              type: 'singles',
              set_code: collection.code,
            }}
          />
        )}
      </section>
    </section>
  )
}
