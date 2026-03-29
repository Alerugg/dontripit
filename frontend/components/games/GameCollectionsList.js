'use client'

import './GameCollectionsList.css'
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

const BLOCKED_CODES_BY_GAME = {
  pokemon: new Set(['base', 'meta', 'promos']),
}

const PAGE_SIZE_BY_MODE = {
  hub: 10,
  full: 24,
}

function normalizeCode(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
}

function normalizeName(value) {
  return String(value || '').trim()
}

function getCardCount(collection) {
  const raw = collection.card_count ?? collection.count ?? collection.total_cards ?? 0
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : 0
}

function getReleaseDate(collection) {
  const raw = collection.release_date || collection.released_at || collection.date || ''
  const parsed = raw ? new Date(raw).getTime() : NaN
  return Number.isNaN(parsed) ? null : parsed
}

function isBlockedCollection(collection, gameSlug) {
  const code = normalizeCode(collection.code || collection.set_code)
  const blocked = BLOCKED_CODES_BY_GAME[gameSlug]

  if (!code) return true
  if (!normalizeName(collection.name || collection.title)) return true
  if (blocked && blocked.has(code)) return true

  return false
}

function dedupeAndSortCollections(collections = [], gameSlug = '') {
  const seen = new Set()

  return collections
    .filter((collection) => !isBlockedCollection(collection, gameSlug))
    .filter((collection) => {
      const code = normalizeCode(collection.code || collection.set_code)
      if (seen.has(code)) return false
      seen.add(code)
      return true
    })
    .sort((a, b) => {
      const aDate = getReleaseDate(a)
      const bDate = getReleaseDate(b)

      if (aDate !== null && bDate !== null && aDate !== bDate) return aDate - bDate
      if (aDate !== null && bDate === null) return -1
      if (aDate === null && bDate !== null) return 1

      const aName = normalizeName(a.name || a.title)
      const bName = normalizeName(b.name || b.title)

      return aName.localeCompare(bName, 'es', { sensitivity: 'base' })
    })
}

function buildSetHref(gameSlug, setCode) {
  return `/games/${gameSlug}/sets/${encodeURIComponent(normalizeCode(setCode))}`
}

function buildAllSetsHref(gameSlug) {
  return `/games/${gameSlug}/sets`
}

function buildSetImageSrc(gameSlug, setCode) {
  const code = normalizeCode(setCode)
  if (!code) return ''
  return `/sets/${gameSlug}/${code}.png`
}

function CollectionLogo({ gameSlug, code, name }) {
  const [broken, setBroken] = useState(false)
  const src = buildSetImageSrc(gameSlug, code)

  return (
    <div className="game-collection-media">
      {!broken && src ? (
        <img
          src={src}
          alt={name}
          className="game-collection-logo"
          loading="lazy"
          onError={() => setBroken(true)}
        />
      ) : null}

      {(broken || !src) ? (
        <div className="game-collection-media-fallback">
          <span>{String(code || '').toUpperCase()}</span>
        </div>
      ) : null}
    </div>
  )
}

function buildPageNumbers(currentPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1)
  }

  if (currentPage <= 4) return [1, 2, 3, 4, 5, '…', totalPages]
  if (currentPage >= totalPages - 3) {
    return [1, '…', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  }

  return [1, '…', currentPage - 1, currentPage, currentPage + 1, '…', totalPages]
}

export default function GameCollectionsList({
  collections = [],
  gameSlug = '',
  mode = 'hub',
}) {
  const safeGameSlug = normalizeCode(gameSlug)
  const safeMode = mode === 'full' ? 'full' : 'hub'
  const pageSize = PAGE_SIZE_BY_MODE[safeMode]

  const allCollections = useMemo(
    () => dedupeAndSortCollections(collections, safeGameSlug),
    [collections, safeGameSlug],
  )

  const [page, setPage] = useState(1)

  useEffect(() => {
    setPage(1)
  }, [safeGameSlug, safeMode, allCollections.length])

  const totalPages = Math.max(1, Math.ceil(allCollections.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const start = (currentPage - 1) * pageSize
  const end = start + pageSize
  const visibleCollections = allCollections.slice(start, end)
  const pageNumbers = buildPageNumbers(currentPage, totalPages)

  function goToPage(nextPage) {
    const bounded = Math.max(1, Math.min(totalPages, nextPage))
    setPage(bounded)

    if (typeof window !== 'undefined') {
      const top = safeMode === 'full' ? 0 : window.scrollY - 120
      window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    }
  }

  return (
    <section className={`game-collections-block panel ${safeMode === 'full' ? 'is-full' : 'is-hub'}`}>
      <div className="game-collections-head">
        <div>
          <p className="eyebrow">Colecciones</p>
          <h2>{safeMode === 'full' ? 'Todas las colecciones' : 'Explora por set o colección'}</h2>
        </div>

        <div className="game-collections-head-actions">
          <p className="game-collections-copy">
            {safeMode === 'full'
              ? 'Archivo completo de colecciones ordenado por lanzamiento. Abre cualquier set y navega su checklist real.'
              : 'Entra a una colección concreta y navega solo las cartas de ese bloque.'}
          </p>

          {safeMode === 'hub' && allCollections.length > pageSize ? (
            <Link href={buildAllSetsHref(safeGameSlug)} className="game-collections-all-link">
              Ver todas las colecciones
            </Link>
          ) : null}
        </div>
      </div>

      {visibleCollections.length === 0 ? (
        <div className="game-collections-empty">
          <p>No hay colecciones reales cargadas todavía para este juego.</p>
        </div>
      ) : (
        <>
          <div className={`game-collections-grid ${safeMode === 'full' ? 'is-full' : 'is-hub'}`}>
            {visibleCollections.map((collection) => {
              const code = normalizeCode(collection.code || collection.set_code)
              const name = normalizeName(collection.name || collection.title)
              const cardCount = getCardCount(collection)

              return (
                <Link
                  key={code}
                  href={buildSetHref(safeGameSlug, code)}
                  className="game-collection-card"
                >
                  <CollectionLogo gameSlug={safeGameSlug} code={code} name={name} />

                  <div className="game-collection-body">
                    <h3>{name}</h3>
                    <p className="game-collection-meta">
                      {code} · {cardCount} cartas
                    </p>
                    <span className="game-collection-cta">Ver colección</span>
                  </div>
                </Link>
              )
            })}
          </div>

          {totalPages > 1 ? (
            <div className="game-collections-footer">
              <span className="game-collections-page-indicator">
                Mostrando {start + 1}-{Math.min(end, allCollections.length)} de {allCollections.length}
              </span>

              <div className="game-collections-pagination">
                <button
                  type="button"
                  className="game-collections-page-btn"
                  onClick={() => goToPage(currentPage - 1)}
                  disabled={currentPage === 1}
                >
                  ←
                </button>

                {pageNumbers.map((value, index) =>
                  value === '…' ? (
                    <span key={`ellipsis-${index}`} className="game-collections-page-ellipsis">
                      …
                    </span>
                  ) : (
                    <button
                      key={value}
                      type="button"
                      className={`game-collections-page-btn ${value === currentPage ? 'is-active' : ''}`}
                      onClick={() => goToPage(value)}
                    >
                      {value}
                    </button>
                  ),
                )}

                <button
                  type="button"
                  className="game-collections-page-btn"
                  onClick={() => goToPage(currentPage + 1)}
                  disabled={currentPage === totalPages}
                >
                  →
                </button>
              </div>

              {safeMode === 'hub' && allCollections.length > pageSize ? (
                <Link href={buildAllSetsHref(safeGameSlug)} className="game-collections-all-link secondary">
                  Ver todas
                </Link>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  )
}