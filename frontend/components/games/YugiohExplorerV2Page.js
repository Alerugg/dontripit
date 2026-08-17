'use client'

import { useEffect, useState } from 'react'
import './GameExplorerPage.css'
import '../searchV2/FacetPicker.css'
import OnePieceSearchV2Experience from '../searchV2/OnePieceSearchV2Experience'
import GameCollectionsList from './GameCollectionsList'
import GameNewsGrid from './GameNewsGrid'
import StatePanel from '../catalog/StatePanel'
import { fetchNewsByGame, fetchSetsByGame } from '../../lib/catalog/client'

export default function YugiohExplorerV2Page({ game }) {
  const [collections, setCollections] = useState([])
  const [collectionsLoading, setCollectionsLoading] = useState(true)
  const [collectionsError, setCollectionsError] = useState('')
  const [news, setNews] = useState([])

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
    <section className="page-shell game-page game-page-pilot" style={{ '--game-accent': game.accent }}>
      <header className="game-hero panel game-hero-pilot" style={{ '--game-accent': game.accent }}>
        <div className="game-hero-copy">
          <p className="eyebrow">{game.eyebrow}</p>
          <h1>{game.name}</h1>
          <p>
            Busca cualquier carta Yu-Gi-Oh! por nombre, release o código de impresión. Advanced Search baja al Print exacto usando únicamente dimensiones respaldadas por la fuente canónica.
          </p>

          <div className="game-hero-insights">
            <div className="hero-insight-chip">
              <span>14.479 Cards</span>
              <strong>Identidad lógica canónica</strong>
            </div>
            <div className="hero-insight-chip">
              <span>44.226 Prints</span>
              <strong>Impresiones físicas certificadas</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Search V2</span>
              <strong>20 facets · 19 activos</strong>
            </div>
          </div>
        </div>

        <div className="game-hero-meta panel-soft game-hero-meta-pilot">
          <article className="hero-card panel-soft explorer-preview-card">
            <span>Search V2</span>
            <strong>Busca “Dark Magician”, “Blue-Eyes White Dragon” o un código como “2017-EN001”.</strong>
            <small>La búsqueda normal agrupa reimpresiones por carta; Advanced Search resuelve el Print exacto por release, rareza, clase, atributo y estadísticas.</small>
          </article>
        </div>
      </header>

      <OnePieceSearchV2Experience game={game} />

      {collectionsLoading ? (
        <StatePanel title="Cargando colecciones" description="Preparando los releases canónicos de Yu-Gi-Oh!." tone="muted" />
      ) : null}

      {!collectionsLoading && collectionsError ? (
        <StatePanel title="No pudimos cargar las colecciones" description={collectionsError} error tone="error" />
      ) : null}

      {!collectionsLoading && !collectionsError ? (
        <GameCollectionsList collections={collections} gameSlug={game.slug} />
      ) : null}

      <GameNewsGrid news={news} />
    </section>
  )
}
