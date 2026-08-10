'use client'

import { useEffect, useState } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import OnePieceSearchV2Experience from '../searchV2/OnePieceSearchV2Experience'
import GameCollectionsList from './GameCollectionsList'
import GameNewsGrid from './GameNewsGrid'
import MarketProductShelf from './MarketProductShelf'
import StatePanel from '../catalog/StatePanel'
import { fetchMarketProductsByGame, fetchNewsByGame, fetchReleasesByGame, fetchSetsByGame } from '../../lib/catalog/client'
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

const REGION_LABELS = {
  GLOBAL: 'Global',
  US: 'USA',
  EU: 'Europa',
  JP: 'Japón',
  EN: 'Internacional',
}

function formatReleaseDate(item) {
  if (!item?.release_date) return ''
  try {
    return new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(`${item.release_date}T12:00:00`))
  } catch {
    return item.release_date
  }
}

export default function GameHubPage({ game }) {
  const copy = GAME_HUB_COPY[game.slug] || { intro: game.description }
  const [collections, setCollections] = useState([])
  const [collectionsLoading, setCollectionsLoading] = useState(true)
  const [collectionsError, setCollectionsError] = useState('')
  const [news, setNews] = useState([])
  const [newsLoading, setNewsLoading] = useState(true)
  const [releases, setReleases] = useState([])
  const [releasesLoading, setReleasesLoading] = useState(true)
  const [marketProducts, setMarketProducts] = useState([])
  const [marketLoading, setMarketLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    setCollectionsLoading(true)
    setCollectionsError('')
    setNewsLoading(true)
    setReleasesLoading(true)
    setMarketLoading(true)

    const collectionsRequest = fetchSetsByGame(game.slug, { limit: 50 })
      .then((items) => {
        if (!cancelled) setCollections(items)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setCollections([])
          setCollectionsError(requestError.message || 'No pudimos cargar los sets.')
        }
      })
      .finally(() => {
        if (!cancelled) setCollectionsLoading(false)
      })

    const releasesRequest = fetchReleasesByGame(game.slug, { limit: 8 })
      .then((items) => { if (!cancelled) setReleases(items) })
      .catch(() => { if (!cancelled) setReleases([]) })
      .finally(() => { if (!cancelled) setReleasesLoading(false) })

    const marketRequest = fetchMarketProductsByGame(game.slug, { limit: 24 })
      .then((items) => { if (!cancelled) setMarketProducts(items) })
      .catch(() => { if (!cancelled) setMarketProducts([]) })
      .finally(() => { if (!cancelled) setMarketLoading(false) })

    const newsRequest = fetchNewsByGame(game.slug, { limit: 6 })
      .then((items) => { if (!cancelled) setNews(items) })
      .catch(() => { if (!cancelled) setNews([]) })
      .finally(() => { if (!cancelled) setNewsLoading(false) })

    Promise.allSettled([collectionsRequest, releasesRequest, marketRequest, newsRequest])

    return () => { cancelled = true }
  }, [game.slug])

  return (
    <section className={`dri-game-hub dri-game-hub-${game.slug}`} style={{ '--game-accent': game.accent }}>
      <div className="app-shell dri-game-hub-shell">
        <header className="v4-game-header">
          <Link href="/#games" className="v4-back-link">← Todos los juegos</Link>
          <div className="v4-game-header-main">
            {copy.logo ? (
              <div className="v4-game-header-logo">
                <Image src={copy.logo} alt={game.name} width={280} height={100} sizes="240px" priority />
              </div>
            ) : <h1>{game.name}</h1>}
            <div>
              <span className="v4-overline"><i /> Catálogo Don’tRipIt</span>
              <h1>Busca una carta sin perderte en el catálogo.</h1>
              <p>{copy.intro}</p>
            </div>
          </div>
          <nav className="v4-game-jumps" aria-label={`Secciones de ${game.name}`}>
            <a href="#buscar">Buscar</a>
            <a href="#colecciones">Sets</a>
            <a href="#sellado">Sellado</a>
            <a href="#lanzamientos">Lanzamientos</a>
            <a href="#noticias">Noticias</a>
          </nav>
        </header>

        <div id="buscar" className="dri-hub-anchor">
          <OnePieceSearchV2Experience game={game} />
        </div>

        <div className="ux-game-secondary">
          <div id="colecciones" className="dri-hub-anchor">
            {collectionsLoading ? (
              <StatePanel title="Cargando sets" description={`Preparando los sets de ${game.name}.`} tone="muted" loading />
            ) : null}
            {!collectionsLoading && collectionsError ? (
              <StatePanel title="No pudimos cargar los sets" description={collectionsError} error tone="error" />
            ) : null}
            {!collectionsLoading && !collectionsError ? (
              <GameCollectionsList collections={collections} gameSlug={game.slug} />
            ) : null}
          </div>

          <div id="sellado" className="dri-hub-anchor">
            {marketLoading ? (
              <StatePanel title="Cargando sellado" description="Consultando productos y precios Cardmarket…" tone="muted" loading />
            ) : (
              <MarketProductShelf products={marketProducts} gameName={game.name} />
            )}
          </div>

          <section id="lanzamientos" className="ux-upcoming-section dri-hub-anchor">
            <div className="ux-section-head">
              <div>
                <span className="v4-overline"><i /> Próximos lanzamientos</span>
                <h2>Fechas que sí están verificadas</h2>
                <p>Región y fuente oficial, siempre visibles.</p>
              </div>
            </div>

            {releasesLoading ? (
              <StatePanel title="Cargando lanzamientos" description="Consultando el calendario oficial…" tone="muted" loading />
            ) : releases.length ? (
              <div className="ux-upcoming-grid">
                {releases.map((item) => (
                  <a
                    key={item.id}
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ux-upcoming-card"
                  >
                    <span>{formatReleaseDate(item)}</span>
                    <strong>{item.title}</strong>
                    <small>{REGION_LABELS[item.region] || item.region} · {item.source}</small>
                    <b>Fuente oficial ↗</b>
                  </a>
                ))}
              </div>
            ) : (
              <div className="dri-soft-empty">
                <strong>No hay una fecha futura que podamos verificar ahora mismo.</strong>
                <p>No usaremos fechas antiguas del catálogo ni una fecha de otra región para rellenar este bloque.</p>
              </div>
            )}
          </section>

          <div id="noticias" className="dri-hub-anchor">
            {newsLoading ? (
              <StatePanel title="Cargando noticias" description="Actualizando las fuentes oficiales de esta región…" tone="muted" loading />
            ) : (
              <GameNewsGrid news={news} />
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
