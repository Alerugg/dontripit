'use client'

import './GameCollectionsDirectoryPage.css'
import { useEffect, useState } from 'react'
import GameCollectionsList from './GameCollectionsList'
import StatePanel from '../catalog/StatePanel'
import { fetchSetsByGame } from '../../lib/catalog/client'

export default function GameCollectionsDirectoryPage({ game }) {
  const [collections, setCollections] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadCollections() {
      setLoading(true)
      setError('')

      try {
        const items = await fetchSetsByGame(game.slug, { limit: 500 })
        if (!cancelled) setCollections(items)
      } catch (requestError) {
        if (!cancelled) {
          setCollections([])
          setError(requestError.message || 'No pudimos cargar el archivo de colecciones.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadCollections()

    return () => {
      cancelled = true
    }
  }, [game.slug])

  return (
    <section className="page-shell game-collections-directory-page">
      <header className="game-collections-directory-hero panel" style={{ '--game-accent': game.accent }}>
        <div className="game-collections-directory-copy">
          <p className="eyebrow">Archivo de sets</p>
          <h1>{game.name}</h1>
          <p>
            Explora todas las colecciones reales del juego, ordenadas por lanzamiento y listas para abrir su checklist.
          </p>
        </div>
      </header>

      {loading ? (
        <StatePanel
          title="Cargando colecciones"
          description="Estamos preparando el archivo completo del juego."
        />
      ) : null}

      {!loading && error ? (
        <StatePanel
          title="No pudimos cargar las colecciones"
          description={error}
          error
        />
      ) : null}

      {!loading && !error ? (
        <GameCollectionsList
          collections={collections}
          gameSlug={game.slug}
          mode="full"
        />
      ) : null}
    </section>
  )
}