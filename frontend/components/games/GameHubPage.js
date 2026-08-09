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
    intro: 'Busca primero la carta. Después, solo si hace falta, elige set, rareza, idioma o variante física.',
  },
  magic: {
    logo: '/games/magic/magic_logo.png',
    intro: 'Empieza por el nombre que conoces y baja a la impresión concreta cuando set, finish o idioma realmente importen.',
  },
  onepiece: {
    logo: '/games/onepiece/onepiece_logo.png',
    intro: 'Encuentra Leaders, Characters, promos y variantes sin tener que descifrar primero toda la estructura del catálogo.',
  },
  yugioh: {
    logo: '/games/yugioh/yugioh_logo.png',
    intro: 'Busca la carta por nombre o código y afina después por set, rareza, atributo o edición concreta.',
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
  const copy = GAME_HUB_COPY[game.slug] || { intro: game.description }
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
          setCollectionsError(requestError.message || 'No pudimos cargar los sets.')
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
        <header className="ux-game-intro">
          <div>
            <Link href="/dashboard#juegos" className="ux-back-link">← Todos los juegos</Link>
            <div className="ux-game-intro-main">
              {copy.logo ? (
                <div className="ux-game-logo-wrap">
                  <img src={copy.logo} alt={game.name} />
                </div>
              ) : <h1>{game.name}</h1>}
              <div className="ux-game-intro-copy">
                <span className="dri-kicker">{game.name}</span>
                <h1>Busca una carta sin perderte en el catálogo.</h1>
                <p>{copy.intro}</p>
              </div>
            </div>
          </div>
        </header>

        <div id="buscar" className="dri-hub-anchor">
          <OnePieceSearchV2Experience game={game} />
        </div>

        <div className="ux-game-secondary">
          <section id="lanzamientos" className="ux-upcoming-section dri-hub-anchor">
            <div className="ux-section-head">
              <div>
                <span className="dri-kicker">Próximos lanzamientos</span>
                <h2>Lo siguiente que merece estar en tu radar</h2>
              </div>
            </div>

            {upcoming.length ? (
              <div className="ux-upcoming-grid">
                {upcoming.map((item) => {
                  const code = String(item.code || item.set_code || '').toLowerCase()
                  return (
                    <Link key={`${code}-${item.name}`} href={`/games/${game.slug}/sets/${encodeURIComponent(code)}`} className="ux-upcoming-card">
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
                <strong>Calendario regional en preparación.</strong>
                <p>Aquí mostraremos fechas oficiales separadas por región cuando la fuente esté validada.</p>
              </div>
            )}
          </section>

          <div id="colecciones" className="dri-hub-anchor">
            {collectionsLoading ? (
              <StatePanel title="Cargando sets" description={`Preparando los sets de ${game.name}.`} tone="muted" />
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
      </div>
    </section>
  )
}
