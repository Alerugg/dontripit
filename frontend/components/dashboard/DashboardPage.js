'use client'

import Link from 'next/link'
import Image from 'next/image'
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import TopNav from '../layout/TopNav'
import FallbackImage from '../common/FallbackImage'
import { GAME_CATALOG } from '../../lib/catalog/games'
import { fetchNewsByGame, fetchReleasesByGame } from '../../lib/catalog/client'
import './DashboardPage.css'
import './DashboardV2.css'

const SEARCH_EXAMPLES = {
  pokemon: 'Pikachu, Charizard, 151…',
  magic: 'Black Lotus, Sol Ring, Commander…',
  onepiece: 'Luffy, Zoro, OP05-119…',
  yugioh: 'Dark Magician, Blue-Eyes, 2017-EN001…',
}

const REGION_LABELS = {
  GLOBAL: 'Global',
  US: 'USA',
  EU: 'Europa',
  JP: 'Japón',
  EN: 'Internacional',
}

function money(value, currency = 'EUR', maximumFractionDigits = 2) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency,
      maximumFractionDigits,
    }).format(number)
  } catch {
    return `${number.toFixed(2)} ${currency}`
  }
}

function shortDate(value, dateOnly = false) {
  if (!value) return ''
  try {
    const source = dateOnly ? `${value}T12:00:00` : value
    return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short' }).format(new Date(source))
  } catch {
    return ''
  }
}

function GameCard({ game }) {
  const soon = game.slug === 'riftbound'
  const logo = `/games/${game.slug}/${game.slug === 'magic' ? 'magic_logo' : `${game.slug}_logo`}.png`
  return (
    <Link
      href={soon ? '#' : `/games/${game.slug}`}
      className={`ux-game-card ${soon ? 'is-soon' : ''}`}
      style={{ '--game-accent': game.accent }}
      aria-disabled={soon}
    >
      <span className="v4-game-state">{soon ? 'Próximamente' : 'Catálogo'}</span>
      <Image src={logo} alt={game.name} width={220} height={80} sizes="160px" />
      <strong>{soon ? 'En preparación' : 'Abrir →'}</strong>
    </Link>
  )
}

function RecentPrintCard({ item }) {
  const print = item?.print || {}
  const meta = [
    print.set_code?.toUpperCase(),
    print.collector_number ? `#${print.collector_number}` : null,
    print.language?.toUpperCase(),
    print.is_foil ? 'Foil' : null,
  ].filter(Boolean).join(' · ')

  return (
    <Link href={`/prints/${print.id}`} className="v13-recent-print">
      <div className="v13-recent-media">
        <FallbackImage
          src={print.image_url}
          alt={print.card_name || 'Carta'}
          className="detail-image"
          placeholderClassName="image-fallback"
          label={print.game || 'TCG'}
        />
        <span>Print {print.id}</span>
      </div>
      <div>
        <strong>{print.card_name || 'Carta'}</strong>
        <small>{meta || 'Impresión física exacta'}</small>
      </div>
    </Link>
  )
}

function WishlistRadarCard({ item }) {
  const print = item?.print || {}
  const price = item?.latest_price || null
  const currentCurrency = String(price?.currency || '').toUpperCase()
  const targetCurrency = String(item?.target_currency || '').toUpperCase()
  const currentValue = Number(price?.value)
  const targetValue = Number(item?.target_price)
  const hasCurrent = price?.value !== null && price?.value !== undefined && Number.isFinite(currentValue)
  const hasTarget = item?.target_price !== null && item?.target_price !== undefined && Number.isFinite(targetValue)
  const comparable = hasCurrent && hasTarget && currentCurrency && targetCurrency && currentCurrency === targetCurrency
  const reached = comparable && currentValue <= targetValue
  const currentLabel = hasCurrent ? money(currentValue, currentCurrency) : null
  const targetLabel = hasTarget && targetCurrency ? money(targetValue, targetCurrency) : null

  return (
    <Link href={`/prints/${print.id}`} className={`v13-radar-card ${reached ? 'is-reached' : ''}`}>
      <div className="v13-radar-main">
        <span className="v13-radar-priority">P{Number(item.priority || 0)}</span>
        <div>
          <strong>{print.card_name || 'Carta'}</strong>
          <small>{[print.set_code?.toUpperCase(), print.collector_number ? `#${print.collector_number}` : null, print.language?.toUpperCase()].filter(Boolean).join(' · ')}</small>
        </div>
      </div>
      <div className="v13-radar-market">
        <span>{currentLabel ? `Actual ${currentLabel}` : 'Sin precio actual exacto'}</span>
        <span>{targetLabel ? `Objetivo ${targetLabel}` : 'Sin objetivo'}</span>
        {comparable ? <b>{reached ? 'Objetivo alcanzado' : `${money(currentValue - targetValue, currentCurrency)} sobre objetivo`}</b> : null}
      </div>
    </Link>
  )
}

function PulseRelease({ item }) {
  const href = item?.source_url || ''
  const content = (
    <>
      <span>{shortDate(item?.release_date, true) || 'Fecha pendiente'}</span>
      <strong>{item?.title || 'Lanzamiento'}</strong>
      <small>{[REGION_LABELS[item?.region] || item?.region, item?.source].filter(Boolean).join(' · ')}</small>
    </>
  )
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="v13-pulse-item">{content}</a> : <article className="v13-pulse-item">{content}</article>
}

function PulseNews({ item }) {
  const href = item?.source_url || item?.href || item?.url || item?.link || ''
  const published = shortDate(item?.date || item?.published_at)
  const content = (
    <>
      <span>{published || 'Fecha no publicada'}</span>
      <strong>{item?.title || 'Noticia oficial'}</strong>
      <small>{[REGION_LABELS[item?.region] || item?.region, item?.source].filter(Boolean).join(' · ') || 'Fuente oficial'}</small>
    </>
  )
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="v13-pulse-item">{content}</a> : <article className="v13-pulse-item">{content}</article>
}

export default function DashboardPage() {
  const router = useRouter()
  const activeGames = useMemo(() => GAME_CATALOG.filter((game) => game.slug !== 'riftbound'), [])
  const [selectedGame, setSelectedGame] = useState('onepiece')
  const [searchQuery, setSearchQuery] = useState('')
  const [user, setUser] = useState(null)
  const [collection, setCollection] = useState({ items: [], count: 0, known_value_eur: 0, valuation_coverage_count: 0 })
  const [wishlist, setWishlist] = useState({ items: [], count: 0 })
  const [loading, setLoading] = useState(true)
  const [news, setNews] = useState([])
  const [releases, setReleases] = useState([])
  const [pulseLoading, setPulseLoading] = useState(true)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState('')

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const responses = await Promise.all([
          fetch('/api/auth/me', { cache: 'no-store' }),
          fetch('/api/library/collection', { cache: 'no-store' }),
          fetch('/api/library/wishlist', { cache: 'no-store' }),
        ])
        if (responses[0].status === 401) {
          window.location.assign('/login')
          return
        }
        const [me, own, wish] = await Promise.all(responses.map((response) => response.json().catch(() => ({}))))
        if (!cancelled) {
          setUser(me.user || null)
          setCollection(own.items ? own : { items: [], count: 0, known_value_eur: 0, valuation_coverage_count: 0 })
          setWishlist(wish.items ? wish : { items: [], count: 0 })
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    setPulseLoading(true)
    Promise.all([
      fetchReleasesByGame(selectedGame, { limit: 3 }).catch(() => []),
      fetchNewsByGame(selectedGame, { limit: 3 }).catch(() => []),
    ]).then(([nextReleases, nextNews]) => {
      if (!cancelled) {
        setReleases(nextReleases)
        setNews(nextNews)
      }
    }).finally(() => {
      if (!cancelled) setPulseLoading(false)
    })
    return () => { cancelled = true }
  }, [selectedGame])

  const pieces = useMemo(() => collection.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0), [collection])
  const recent = collection.items.slice(0, 4)
  const radar = wishlist.items.slice(0, 4)
  const selectedGameConfig = activeGames.find((game) => game.slug === selectedGame) || activeGames[0]
  const coverage = collection.count > 0 ? Math.round((Number(collection.valuation_coverage_count || 0) / Number(collection.count)) * 100) : 0
  const valueLabel = money(collection.known_value_eur || 0, 'EUR', 0) || '0 €'
  const canDeleteAccount = deletePassword.length > 0 && deleteConfirmation.trim().toUpperCase() === 'ELIMINAR' && !deleteBusy

  function submitSearch(event) {
    event.preventDefault()
    const q = searchQuery.trim()
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    params.set('kind', 'card')
    params.set('view', 'grid')
    router.push(`/games/${selectedGame}?${params.toString()}#buscar`)
  }

  async function deleteAccount(event) {
    event.preventDefault()
    if (!canDeleteAccount) return
    setDeleteBusy(true)
    setDeleteError('')
    try {
      const response = await fetch('/api/auth/me', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          password: deletePassword,
          confirmation: deleteConfirmation.trim().toUpperCase(),
        }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        setDeleteError(payload?.message || 'No pudimos eliminar la cuenta. Revisa la contraseña e inténtalo de nuevo.')
        return
      }
      window.location.assign('/?account=deleted')
    } catch {
      setDeleteError('No pudimos conectar con el servicio de cuenta. Inténtalo de nuevo.')
    } finally {
      setDeleteBusy(false)
    }
  }

  function closeDeletePanel() {
    if (deleteBusy) return
    setDeleteOpen(false)
    setDeletePassword('')
    setDeleteConfirmation('')
    setDeleteError('')
  }

  return (
    <main>
      <TopNav />
      <section className="ux-dashboard v13-dashboard">
        <header className="v13-dashboard-head">
          <div>
            <span className="v4-overline"><i /> {loading ? 'Preparando tu espacio' : `Hola, ${user?.name?.split(' ')[0] || 'coleccionista'}`}</span>
            <h1>Tu colección, de un vistazo.</h1>
            <p>Identidades físicas exactas, valor conocido y lo que estás siguiendo. Sin mezclar ediciones.</p>
          </div>
          <div className="v13-head-actions">
            <Link href="/collection" className="dri-btn dri-btn-primary">Mi colección</Link>
            <Link href="/wishlist" className="dri-btn dri-btn-ghost">Wishlist</Link>
          </div>
        </header>

        <section className="ux-overview v13-overview" aria-label="Resumen de tu cuenta">
          <Link href="/collection" className="ux-stat"><span>Prints distintas</span><strong>{collection.count || 0}</strong><small>Identidades físicas guardadas</small></Link>
          <Link href="/collection" className="ux-stat"><span>Cartas físicas</span><strong>{pieces}</strong><small>Incluye cantidades repetidas</small></Link>
          <Link href="/wishlist" className="ux-stat"><span>Wishlist</span><strong>{wishlist.count || 0}</strong><small>Prints que estás siguiendo</small></Link>
          <Link href="/collection" className="ux-stat v13-value-stat"><span>Valor conservador*</span><strong>{valueLabel}</strong><small>Cobertura {collection.valuation_coverage_count || 0}/{collection.count || 0} ({coverage}%). El resto no se estima.</small></Link>
        </section>

        <section id="buscar" className="ux-search-hero v13-search-workspace">
          <div className="ux-search-copy">
            <span className="v4-overline"><i /> Catálogo</span>
            <h2>Busca una carta.</h2>
            <p>Elige el juego y entra primero en la carta canónica; desde ahí bajas a la impresión exacta.</p>

            <div className="ux-game-tabs" aria-label="Selecciona un juego">
              {activeGames.map((game) => (
                <button
                  key={game.slug}
                  type="button"
                  className={`ux-game-tab ${selectedGame === game.slug ? 'is-active' : ''}`}
                  onClick={() => setSelectedGame(game.slug)}
                  aria-pressed={selectedGame === game.slug}
                >
                  {game.name}
                </button>
              ))}
              <button type="button" className="ux-game-tab" disabled>Riftbound · Próximamente</button>
            </div>

            <form className="ux-main-search" onSubmit={submitSearch}>
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder={`Busca en ${selectedGameConfig?.name || 'tu TCG'}…`}
                aria-label={`Buscar cartas en ${selectedGameConfig?.name || 'el juego seleccionado'}`}
                autoComplete="off"
              />
              <button type="submit" className="dri-btn dri-btn-primary">Buscar</button>
            </form>
            <small className="ux-search-hint">Ejemplos: {SEARCH_EXAMPLES[selectedGame] || 'nombre, número o set'}</small>
          </div>
        </section>

        {!loading && collection.count === 0 ? (
          <section className="ux-first-step v13-first-step">
            <div>
              <span className="v4-overline"><i /> Tu primer paso</span>
              <h2>Añade tu primera Print.</h2>
              <p>Busca una carta, elige la impresión física que realmente tienes y guárdala.</p>
            </div>
            <Link href={`/games/${selectedGame}#buscar`} className="dri-btn dri-btn-primary">Empezar →</Link>
          </section>
        ) : null}

        <div className="v13-personal-grid">
          <section className="v13-personal-panel">
            <div className="v13-panel-head">
              <div><span className="v4-overline"><i /> Colección</span><h2>Últimas Prints</h2></div>
              <Link href="/collection">Ver todas →</Link>
            </div>
            {recent.length ? (
              <div className="v13-recent-grid">{recent.map((item) => <RecentPrintCard key={item.id} item={item} />)}</div>
            ) : (
              <div className="v13-panel-empty"><span>Aún no has guardado ninguna Print.</span><Link href={`/games/${selectedGame}#buscar`}>Explorar catálogo →</Link></div>
            )}
          </section>

          <section className="v13-personal-panel">
            <div className="v13-panel-head">
              <div><span className="v4-overline"><i /> Wishlist</span><h2>Tu radar</h2></div>
              <Link href="/wishlist">Abrir wishlist →</Link>
            </div>
            {radar.length ? (
              <div className="v13-radar-list">{radar.map((item) => <WishlistRadarCard key={item.id} item={item} />)}</div>
            ) : (
              <div className="v13-panel-empty"><span>No sigues ninguna Print todavía.</span><Link href={`/games/${selectedGame}#buscar`}>Buscar una →</Link></div>
            )}
          </section>
        </div>

        <section className="v13-pulse-section" aria-label={`Pulso oficial de ${selectedGameConfig?.name || 'tu TCG'}`}>
          <div className="v13-pulse-head">
            <div>
              <span className="v4-overline"><i /> Fuentes oficiales</span>
              <h2>Pulso de {selectedGameConfig?.name}</h2>
              <p>Próximos lanzamientos y noticias verificadas del juego que tienes seleccionado arriba.</p>
            </div>
            <Link href={`/games/${selectedGame}`} className="home-inline-link">Abrir hub →</Link>
          </div>

          {pulseLoading ? <div className="v13-pulse-loading">Actualizando fuentes oficiales…</div> : (
            <div className="v13-pulse-grid">
              <div className="v13-pulse-column">
                <div className="v13-pulse-column-head"><span>Próximos</span><strong>Lanzamientos</strong></div>
                {releases.length ? releases.map((item) => <PulseRelease key={item.id || item.source_url} item={item} />) : <div className="v13-pulse-empty">No hay una fecha futura verificada para mostrar ahora.</div>}
              </div>
              <div className="v13-pulse-column">
                <div className="v13-pulse-column-head"><span>Últimas</span><strong>Noticias oficiales</strong></div>
                {news.length ? news.map((item, index) => <PulseNews key={item.id || item.source_url || `${item.title}-${index}`} item={item} />) : <div className="v13-pulse-empty">No hay publicaciones oficiales verificables para mostrar ahora.</div>}
              </div>
            </div>
          )}
        </section>

        <section id="juegos" className="ux-section v13-catalog-section">
          <div className="ux-section-head">
            <div><span className="v4-overline"><i /> Catálogos</span><h2>Cambiar de juego</h2></div>
          </div>
          <div className="ux-game-grid v13-game-grid">{GAME_CATALOG.map((game) => <GameCard key={game.slug} game={game} />)}</div>
        </section>

        <section className="ux-account-section" aria-labelledby="account-settings-title">
          <div>
            <span className="v4-overline"><i /> Cuenta</span>
            <h2 id="account-settings-title">Privacidad y control</h2>
            <p>La eliminación de cuenta sigue disponible aquí, separada de las tareas normales de colección.</p>
          </div>
          {!deleteOpen ? (
            <button type="button" className="dri-btn dri-btn-ghost ux-danger-trigger" onClick={() => setDeleteOpen(true)}>Eliminar mi cuenta</button>
          ) : (
            <form className="ux-delete-account" onSubmit={deleteAccount}>
              <div className="ux-delete-warning" role="note">
                <strong>Esta acción es definitiva.</strong>
                <p>Se eliminarán tu cuenta, sesiones, colección y wishlist. No podremos recuperar estos datos después.</p>
              </div>
              <label>
                <span>Contraseña actual</span>
                <input type="password" value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} autoComplete="current-password" required />
              </label>
              <label>
                <span>Escribe ELIMINAR para confirmar</span>
                <input type="text" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} autoComplete="off" spellCheck="false" required />
              </label>
              {deleteError ? <p className="ux-delete-error" role="alert">{deleteError}</p> : null}
              <div className="ux-delete-actions">
                <button type="button" className="dri-btn dri-btn-ghost" onClick={closeDeletePanel} disabled={deleteBusy}>Cancelar</button>
                <button type="submit" className="dri-btn ux-danger-button" disabled={!canDeleteAccount}>{deleteBusy ? 'Eliminando…' : 'Eliminar cuenta definitivamente'}</button>
              </div>
            </form>
          )}
        </section>
      </section>
    </main>
  )
}
