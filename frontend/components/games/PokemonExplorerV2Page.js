'use client'

import { useEffect, useState } from 'react'
import './GameExplorerPage.css'
import '../searchV2/FacetPicker.css'
import OnePieceSearchV2Experience from '../searchV2/OnePieceSearchV2Experience'
import GameCollectionsList from './GameCollectionsList'
import GameNewsGrid from './GameNewsGrid'
import StatePanel from '../catalog/StatePanel'
import { fetchNewsByGame, fetchSetsByGame } from '../../lib/catalog/client'

export default function PokemonExplorerV2Page({ game }) {
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
          setNewsError('')
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
            Busca cualquier carta Pokémon por nombre, set o número. Usa Advanced Search para llegar a la impresión física exacta por tipo, etapa, rareza, regulación, ilustrador, finish, stamp y más.
          </p>

          <div className="game-hero-insights">
            <div className="hero-insight-chip">
              <span>21.065 Cards</span>
              <strong>Identidad inglesa canónica</strong>
            </div>
            <div className="hero-insight-chip">
              <span>27.241 Variants</span>
              <strong>Dimensiones físicas certificadas</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Search V2</span>
              <strong>23 filtros específicos de Pokémon</strong>
            </div>
          </div>
        </div>

        <div className="game-hero-meta panel-soft game-hero-meta-pilot">
          <article className="hero-card panel-soft explorer-preview-card">
            <span>Search V2</span>
            <strong>Busca “Pikachu”, “Charizard” o un collector number como lo harías normalmente.</strong>
            <small>La búsqueda normal agrupa la carta; Advanced Search baja al print físico y sus variantes.</small>
          </article>
        </div>
      </header>

      <OnePieceSearchV2Experience game={game} />

      {collectionsLoading ? (
        <StatePanel title="Cargando colecciones" description="Preparando los sets canónicos de Pokémon." tone="muted" />
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
