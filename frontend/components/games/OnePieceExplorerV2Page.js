'use client'

import { useEffect, useState } from 'react'
import './GameExplorerPage.css'
import '../searchV2/FacetPicker.css'
import OnePieceSearchV2Experience from '../searchV2/OnePieceSearchV2Experience'
import GameCollectionsList from './GameCollectionsList'
import GameNewsGrid from './GameNewsGrid'
import StatePanel from '../catalog/StatePanel'
import { fetchNewsByGame, fetchSetsByGame } from '../../lib/catalog/client'

export default function OnePieceExplorerV2Page({ game }) {
  const [collections, setCollections] = useState([])
  const [collectionsLoading, setCollectionsLoading] = useState(true)
  const [collectionsError, setCollectionsError] = useState('')
  const [news, setNews] = useState([])
  const [newsError, setNewsError] = useState('')

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [nextCollections, nextNews] = await Promise.all([
          fetchSetsByGame(game.slug, { limit: 500 }),
          fetchNewsByGame(game.slug, { limit: 6 }).catch(() => []),
        ])
        if (!cancelled) {
          setCollections(nextCollections)
          setNews(nextNews)
          setCollectionsError('')
        }
      } catch (requestError) {
        if (!cancelled) {
          setCollections([])
          setCollectionsError(requestError.message || 'No pudimos cargar las colecciones.')
        }
      } finally {
        if (!cancelled) setCollectionsLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [game.slug])

  return (
    <section className="page-shell game-page" style={{ '--game-accent': game.accent }}>
      <header className="game-hero panel" style={{ '--game-accent': game.accent }}>
        <div className="game-hero-copy">
          <p className="eyebrow">{game.eyebrow}</p>
          <h1>{game.name}</h1>
          <p>
            Encuentra cualquier carta por nombre, número, set o contexto natural. Abre Advanced Search cuando necesites llegar a una impresión física concreta.
          </p>

          <div className="game-hero-insights">
            <div className="hero-insight-chip">
              <span>Normal Search</span>
              <strong>Cards agrupadas, sin ruido</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Advanced</span>
              <strong>Filtros específicos de One Piece</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Identity V2</span>
              <strong>Prints y releases separados</strong>
            </div>
          </div>
        </div>

        <div className="game-hero-meta panel-soft">
          <article className="hero-card panel-soft hero-card-b">
            <span>Search V2</span>
            <strong>Busca “Luffy”, “OP05-119” o “Luffy OP05” como lo harías de forma natural.</strong>
            <small>La búsqueda normal agrupa variantes; Advanced Search baja al print exacto.</small>
          </article>
        </div>
      </header>

      <OnePieceSearchV2Experience game={game} />

      {collectionsLoading ? (
        <StatePanel title="Cargando colecciones" description="Preparando los sets canónicos de One Piece." tone="muted" />
      ) : null}

      {!collectionsLoading && collectionsError ? (
        <StatePanel title="No pudimos cargar las colecciones" description={collectionsError} error tone="error" />
      ) : null}

      {!collectionsLoading && !collectionsError ? (
        <GameCollectionsList collections={collections} gameSlug={game.slug} />
      ) : null}

      {newsError ? <StatePanel title="No pudimos cargar noticias" description={newsError} error tone="error" /> : null}
      <GameNewsGrid news={news} />
    </section>
  )
}
