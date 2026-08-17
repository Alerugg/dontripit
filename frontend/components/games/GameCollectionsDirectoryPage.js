'use client'

import './GameCollectionsDirectoryPage.css'
import { useEffect, useState } from 'react'
import GameCollectionsList from './GameCollectionsList'
import StatePanel from '../catalog/StatePanel'
import { fetchSetsPage } from '../../lib/catalog/client'

const PAGE_SIZE = 24

export default function GameCollectionsDirectoryPage({ game }) {
  const [collections, setCollections] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  useEffect(() => {
    let cancelled = false
    const handle = setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchSetsPage(game.slug, {
          q: query.trim(),
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        })
        if (!cancelled) {
          setCollections(payload.items)
          setTotal(payload.total)
        }
      } catch (requestError) {
        if (!cancelled) {
          setCollections([])
          setTotal(0)
          setError(requestError.message || 'No pudimos cargar el archivo de colecciones.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, query ? 180 : 0)

    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [game.slug, page, query])

  function changeQuery(value) {
    setQuery(value)
    setPage(1)
  }

  return (
    <section className="page-shell game-collections-directory-page">
      <header className="game-collections-directory-hero panel" style={{ '--game-accent': game.accent }}>
        <div className="game-collections-directory-copy">
          <p className="eyebrow">Archivo de sets</p>
          <h1>{game.name}</h1>
          <p>Explora todas las colecciones reales del juego, ordenadas por lanzamiento y paginadas sobre el catálogo completo.</p>
          <label className="game-collections-directory-search">
            <span>Buscar set</span>
            <input value={query} onChange={(event) => changeQuery(event.target.value)} placeholder="Código o nombre…" />
          </label>
        </div>
      </header>

      {loading ? <StatePanel title="Cargando colecciones" description="Consultando esta página del catálogo." /> : null}
      {!loading && error ? <StatePanel title="No pudimos cargar las colecciones" description={error} error /> : null}

      {!loading && !error ? (
        <>
          <div className="game-collections-directory-summary">
            <span>{total.toLocaleString()} set{total === 1 ? '' : 's'}</span>
            <span>Página {page} de {totalPages}</span>
          </div>
          <GameCollectionsList collections={collections} gameSlug={game.slug} mode="full" />
          {totalPages > 1 ? (
            <nav className="game-collections-directory-pagination" aria-label="Paginación de sets">
              <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}>← Anterior</button>
              <span>Página <strong>{page}</strong> de {totalPages}</span>
              <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Siguiente →</button>
            </nav>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
