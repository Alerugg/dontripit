'use client'

import { useEffect, useState } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import CatalogExplorer from '../catalog/CatalogExplorer'
import OnePieceSearchV2Experience from '../searchV2/OnePieceSearchV2Experience'
import GameCollectionsList from './GameCollectionsList'
import GameNewsGrid from './GameNewsGrid'
import MarketProductShelf from './MarketProductShelf'
import StatePanel from '../catalog/StatePanel'
import { fetchMarketProductsByGame, fetchNewsByGame, fetchReleasesByGame, fetchSetsByGame } from '../../lib/catalog/client'
import './GameExplorerPage.css'
import './GameHubV2.css'
import '../searchV2/FacetPicker.css'

const GAME_HUB_COPY = {
  pokemon: {
    logo: '/games/pokemon/pokemon_logo.png',
    intro: 'Busca primero la carta. Después baja a la impresión física exacta cuando idioma, acabado o variante realmente importen.',
  },
  magic: {
    logo: '/games/magic/magic_logo.png',
    intro: 'Empieza por el nombre que conoces y separa carta, set e impresión física sin mezclar identidades de mercado.',
  },
  onepiece: {
    logo: '/games/onepiece/onepiece_logo.png',
    intro: 'Busca Luffy, Zoro o un código. Primero verás las cartas que coinciden; después eliges la impresión física concreta.',
  },
  yugioh: {
    logo: '/games/yugioh/yugioh_logo.png',
    intro: 'Busca por nombre o código y entra en la carta antes de elegir idioma, rareza, edición o variante física.',
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

export default function GameHubPage({ game, initialExplorerState = {} }) {
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
      .then((items) => { if (!cancelled) setCollections(items) })
      .catch((requestError) => {
        if (!cancelled) {
          setCollections([])
          setCollectionsError(requestError.message || 'No pudimos cargar los sets.')
        }
      })
      .finally(() => { if (!cancelled) setCollectionsLoading(false) })

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
    <section className={`dri-game-hub dri-game-hub-${game.slug} v6-game-hub`} style={{ '--game-accent': game.accent }}>
      <div className="app-shell dri-game-hub-shell">
        <header className="v6-game-hero">
          <div className="v6-game-hero-topline">
            <Link href="/#games" className="v6-back-link">← Todos los juegos</Link>
            <span className="v6-live-pill"><i /> Catálogo activo</span>
          </div>
          <div className="v6-game-hero-main">
            <div className="v6-game-brand">
              {copy.logo ? (
                <div className="v6-game-logo">
                  <Image src={copy.logo} alt={game.name} width={300} height={108} sizes="260px" priority />
                </div>
              ) : <h1>{game.name}</h1>}
              <p>{copy.intro}</p>
            </div>
            <div className="v6-game-thesis">
              <span className="v6-eyebrow">Carta → impresión → mercado</span>
              <h1>Encuentra primero la carta correcta.</h1>
              <p>La búsqueda principal agrupa por carta. El precio aparece únicamente cuando abres una impresión física con referencia segura.</p>
            </div>
          </div>
          <nav className="v6-game-jumps" aria-label={`Secciones de ${game.name}`}>
            <a href="#buscar">Explorar</a>
            <a href="#colecciones">Sets</a>
            <a href="#sellado">Sellado</a>
            <a href="#lanzamientos">Lanzamientos</a>
            <a href="#noticias">Noticias</a>
          </nav>
        </header>

        <div id="buscar" className="dri-hub-anchor v6-game-search">
          <CatalogExplorer
            scopedGame={game.slug}
            heading={`Explorar ${game.name}`}
            description="Busca por nombre, número o set. Cambia entre Cartas, Impresiones y Sets sin perder el contexto de la búsqueda."
            kicker="Catálogo"
            allowGameSelect={false}
            compactSidebar
            initialQuery={initialExplorerState.query || ''}
            initialType={initialExplorerState.type || ''}
            initialView={initialExplorerState.view || 'grid'}
            initialSort={initialExplorerState.sort || 'relevance'}
            initialLanguage={initialExplorerState.language || ''}
            initialPricedOnly={Boolean(initialExplorerState.pricedOnly)}
            initialPage={initialExplorerState.page || 1}
          />

          <details className="v6-advanced-search">
            <summary>
              <span className="v6-advanced-search-copy">
                <strong>Búsqueda avanzada de impresiones</strong>
                <small>Busca una carta sin perderte en el catálogo. Abre este panel solo cuando necesites filtrar la identidad física por atributos específicos de {game.name}.</small>
              </span>
              <span className="v6-advanced-search-chevron" aria-hidden="true">⌄</span>
            </summary>
            <div className="v6-advanced-search-body">
              <OnePieceSearchV2Experience game={game} />
            </div>
          </details>
        </div>

        <div className="ux-game-secondary v6-game-secondary">
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
                <span className="v6-eyebrow">Próximos lanzamientos</span>
                <h2>Fechas que sí están verificadas</h2>
                <p>Región y fuente oficial, siempre visibles.</p>
              </div>
            </div>

            {releasesLoading ? (
              <StatePanel title="Cargando lanzamientos" description="Consultando el calendario oficial…" tone="muted" loading />
            ) : releases.length ? (
              <div className="ux-upcoming-grid">
                {releases.map((item) => (
                  <a key={item.id} href={item.source_url} target="_blank" rel="noopener noreferrer" className="ux-upcoming-card">
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
                <p>No usamos fechas antiguas ni de otra región para rellenar este bloque.</p>
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
