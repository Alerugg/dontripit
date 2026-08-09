'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import OnePieceSearchV2Experience from '../searchV2/OnePieceSearchV2Experience'
import GameCollectionsList from './GameCollectionsList'
import GameNewsGrid from './GameNewsGrid'
import StatePanel from '../catalog/StatePanel'
import { fetchNewsByGame, fetchSetsByGame } from '../../lib/catalog/client'
import './GameExplorerPage.css'
import '../searchV2/FacetPicker.css'

const GAME_HUB_COPY = {
  pokemon: {
    logo: '/games/pokemon/pokemon_logo.png',
    intro: 'Encuentra una carta por nombre, número o set y abre sus versiones solo cuando quieras llegar a la impresión exacta.',
    examples: ['Pikachu', 'Charizard', '151'],
  },
  magic: {
    logo: '/games/magic/magic_logo.png',
    intro: 'Busca la carta primero. Después, si lo necesitas, baja a set, idioma, finish y la impresión física exacta.',
    examples: ['Black Lotus', 'Lightning Bolt', 'Sol Ring'],
  },
  onepiece: {
    logo: '/games/onepiece/onepiece_logo.png',
    intro: 'Leaders, Characters, promos y variantes organizados para que encontrar una carta no se convierta en una investigación.',
    examples: ['Luffy', 'Zoro', 'OP05-119'],
  },
  yugioh: {
    logo: '/games/yugioh/yugioh_logo.png',
    intro: 'Empieza por la carta que conoces y llega a la edición exacta cuando rareza, set o código realmente importen.',
    examples: ['Dark Magician', 'Blue-Eyes', 'Ash Blossom'],
  },
}

function releaseTimestamp(item) {
  const raw = item?.release_date || item?.released_at || item?.date
  const value = raw ? new Date(raw).getTime() : NaN
  return Number.isNaN(value) ? null : value
}

function formatReleaseDate(item) {
  const timestamp = releaseTimestamp(item)
  if (!timestamp) return ''
  return new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(timestamp))
}

export default function GameHubPage({ game }) {
  const copy = GAME_HUB_COPY[game.slug] || { intro: game.description, examples: [] }
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

  const upcoming = useMemo(() => {
    const now = Date.now() - (24 * 60 * 60 * 1000)
    return collections
      .filter((item) => {
        const timestamp = releaseTimestamp(item)
        return timestamp && timestamp >= now
      })
      .sort((a, b) => releaseTimestamp(a) - releaseTimestamp(b))
      .slice(0, 4)
  }, [collections])

  return (
    <section className={`dri-game-hub dri-game-hub-${game.slug}`} style={{ '--game-accent': game.accent }}>
      <div className="app-shell dri-game-hub-shell">
        <header className="dri-game-hub-hero">
          <div className="dri-game-hub-copy">
            <Link href="/" className="dri-game-back">← Todos los juegos</Link>
            {copy.logo ? (
              <div className="dri-game-hub-logo-wrap">
                <img src={copy.logo} alt={game.name} className="dri-game-hub-logo" />
              </div>
            ) : <h1>{game.name}</h1>}
            <p>{copy.intro}</p>
            <div className="dri-game-hub-actions">
              <a href="#buscar" className="dri-btn dri-btn-primary dri-btn-lg">Buscar cartas</a>
              <a href="#colecciones" className="dri-btn dri-btn-ghost dri-btn-lg">Ver sets</a>
            </div>
          </div>

          <div className="dri-game-hub-visual" aria-hidden="true">
            <div className="dri-hub-search-preview">
              <span>⌕</span>
              <strong>{copy.examples?.[0] || 'Buscar una carta'}</strong>
              <kbd>↵</kbd>
            </div>
            <div className="dri-hub-card-stack">
              {(copy.examples || []).slice(0, 3).map((example, index) => (
                <article key={example} className={`dri-hub-sample-card dri-hub-sample-${index + 1}`}>
                  <div className="dri-hub-sample-art"><span>{String(game.name).slice(0, 2).toUpperCase()}</span></div>
                  <small>{game.name}</small>
                  <strong>{example}</strong>
                </article>
              ))}
            </div>
          </div>
        </header>

        <nav className="dri-game-subnav" aria-label={`Secciones de ${game.name}`}>
          <a href="#buscar">Buscar</a>
          <a href="#colecciones">Sets</a>
          <a href="#lanzamientos">Próximos</a>
          <a href="#noticias">Noticias</a>
        </nav>

        <div id="buscar" className="dri-hub-anchor">
          <OnePieceSearchV2Experience game={game} />
        </div>

        <section id="lanzamientos" className="dri-hub-section dri-hub-anchor">
          <div className="dri-hub-section-head">
            <div>
              <span className="dri-kicker">Próximos lanzamientos</span>
              <h2>Lo siguiente que merece estar en tu radar.</h2>
            </div>
            <span className="dri-region-note">Calendario regional en evolución</span>
          </div>

          {upcoming.length ? (
            <div className="dri-upcoming-grid">
              {upcoming.map((item) => {
                const code = String(item.code || item.set_code || '').toLowerCase()
                return (
                  <Link key={`${code}-${item.name}`} href={`/games/${game.slug}/sets/${encodeURIComponent(code)}`} className="dri-upcoming-card">
                    <span>{formatReleaseDate(item)}</span>
                    <strong>{item.name || item.title}</strong>
                    <small>{String(item.code || item.set_code || '').toUpperCase()}</small>
                    <b>Ver set →</b>
                  </Link>
                )
              })}
            </div>
          ) : (
            <div className="dri-soft-empty">
              <strong>Estamos preparando el calendario regional.</strong>
              <p>Este bloque se alimentará de fuentes oficiales y distinguirá Japón, USA y Europa según cada TCG.</p>
            </div>
          )}
        </section>

        <div id="colecciones" className="dri-hub-anchor">
          {collectionsLoading ? (
            <StatePanel title="Cargando sets" description={`Preparando el catálogo de ${game.name}.`} tone="muted" />
          ) : null}
          {!collectionsLoading && collectionsError ? (
            <StatePanel title="No pudimos cargar los sets" description={collectionsError} error tone="error" />
          ) : null}
          {!collectionsLoading && !collectionsError ? (
            <GameCollectionsList collections={collections} gameSlug={game.slug} />
          ) : null}
        </div>

        <div id="noticias" className="dri-hub-anchor">
          <GameNewsGrid news={news} />
        </div>
      </div>
    </section>
  )
}
