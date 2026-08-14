'use client'

import Link from 'next/link'
import Image from 'next/image'
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import TopNav from '../layout/TopNav'
import FallbackImage from '../common/FallbackImage'
import { GAME_CATALOG } from '../../lib/catalog/games'
import './DashboardPage.css'

const SEARCH_EXAMPLES = {
  pokemon: 'Pikachu, Charizard, 151…',
  magic: 'Black Lotus, Sol Ring, Commander…',
  onepiece: 'Luffy, Zoro, OP05-119…',
  yugioh: 'Dark Magician, Blue-Eyes, 2017-EN001…',
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
      <Image src={logo} alt={game.name} width={220} height={80} sizes="180px" />
      <strong>{soon ? 'En preparación' : 'Explorar →'}</strong>
    </Link>
  )
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

  const pieces = useMemo(() => collection.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0), [collection])
  const recent = collection.items.slice(0, 6)
  const selectedGameConfig = activeGames.find((game) => game.slug === selectedGame) || activeGames[0]
  const coverage = collection.count > 0 ? Math.round((Number(collection.valuation_coverage_count || 0) / Number(collection.count)) * 100) : 0
  const valueLabel = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(collection.known_value_eur || 0)
  const canDeleteAccount = deletePassword.length > 0 && deleteConfirmation.trim().toUpperCase() === 'ELIMINAR' && !deleteBusy

  function submitSearch(event) {
    event.preventDefault()
    const q = searchQuery.trim()
    const destination = `/games/${selectedGame}${q ? `?q=${encodeURIComponent(q)}` : ''}#buscar`
    router.push(destination)
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
      <section className="ux-dashboard">
        <section id="buscar" className="ux-search-hero">
          <div className="ux-search-copy">
            <span className="v4-overline"><i /> {loading ? 'Preparando tu espacio' : `Hola, ${user?.name?.split(' ')[0] || 'coleccionista'}`}</span>
            <h1>¿Qué carta buscas?</h1>
            <p>Empieza por el nombre, número o set. Después eliges la versión física exacta sin salir del flujo.</p>

            <div className="ux-game-tabs" aria-label="Selecciona un juego">
              {activeGames.map((game) => (
                <button
                  key={game.slug}
                  type="button"
                  className={`ux-game-tab ${selectedGame === game.slug ? 'is-active' : ''}`}
                  onClick={() => setSelectedGame(game.slug)}
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

        <section className="ux-overview" aria-label="Resumen de tu cuenta">
          <div className="ux-stat"><span>Versiones guardadas</span><strong>{collection.count || 0}</strong><small>Cada edición cuenta una vez</small></div>
          <div className="ux-stat"><span>Cartas totales</span><strong>{pieces}</strong><small>Incluye cantidades repetidas</small></div>
          <div className="ux-stat"><span>Wishlist</span><strong>{wishlist.count || 0}</strong><small>Versiones que estás buscando</small></div>
          <div className="ux-stat"><span>Valor conservador*</span><strong>{valueLabel}</strong><small>Cobertura de precio: {collection.valuation_coverage_count || 0} de {collection.count || 0} versiones ({coverage}%). Las demás no se estiman.</small></div>
        </section>

        {!loading && collection.count === 0 ? (
          <section className="ux-first-step">
            <div>
              <span className="v4-overline"><i /> Tu primer paso</span>
              <h2>Añade tu primera versión.</h2>
              <p>Busca una carta que ya tengas, elige la edición correcta y pulsa “Mi colección”. A partir de ahí este espacio empezará a reflejar tu colección real.</p>
            </div>
            <Link href={`/games/${selectedGame}#buscar`} className="dri-btn dri-btn-primary">Buscar en {selectedGameConfig?.name || 'el catálogo'} →</Link>
          </section>
        ) : null}

        <section id="juegos" className="ux-section">
          <div className="ux-section-head">
            <div>
              <span className="v4-overline"><i /> Catálogos</span>
              <h2>Explorar por juego</h2>
            </div>
          </div>
          <div className="ux-game-grid">
            {GAME_CATALOG.map((game) => <GameCard key={game.slug} game={game} />)}
          </div>
        </section>

        {recent.length ? (
          <section className="ux-section">
            <div className="ux-section-head">
              <div>
                <span className="v4-overline"><i /> Tu colección</span>
                <h2>Lo último que guardaste</h2>
              </div>
              <Link href="/collection" className="home-inline-link">Ver colección completa →</Link>
            </div>
            <div className="ux-recent-grid">
              {recent.map((item) => (
                <Link key={item.id} href={`/prints/${item.print.id}`} className="ux-recent-card">
                  <div className="ux-recent-image">
                    <FallbackImage src={item.print.image_url} alt={item.print.card_name} className="detail-image" placeholderClassName="image-fallback" label={item.print.game} />
                  </div>
                  <strong>{item.print.card_name}</strong>
                  <small>{[item.print.set_code, item.print.collector_number].filter(Boolean).join(' · ')}</small>
                </Link>
              ))}
            </div>
          </section>
        ) : null}

        <section id="account-settings" className="ux-account-section" aria-labelledby="account-settings-title">
          <div>
            <span className="v4-overline"><i /> Cuenta</span>
            <h2 id="account-settings-title">Privacidad y control de tu cuenta</h2>
            <p>Puedes eliminar definitivamente tu cuenta y los datos asociados desde aquí.</p>
            <p><Link href="/delete-account">Ver también la vía pública de eliminación y recuperación de acceso →</Link></p>
          </div>
          {!deleteOpen ? (
            <button type="button" className="dri-btn dri-btn-ghost ux-danger-trigger" onClick={() => setDeleteOpen(true)}>
              Eliminar mi cuenta
            </button>
          ) : (
            <form className="ux-delete-account" onSubmit={deleteAccount}>
              <div className="ux-delete-warning" role="note">
                <strong>Esta acción es definitiva.</strong>
                <p>Se eliminarán tu cuenta, sesiones, colección y wishlist. No podremos recuperar estos datos después.</p>
              </div>
              <label>
                <span>Contraseña actual</span>
                <input
                  type="password"
                  value={deletePassword}
                  onChange={(event) => setDeletePassword(event.target.value)}
                  autoComplete="current-password"
                  required
                />
              </label>
              <label>
                <span>Escribe ELIMINAR para confirmar</span>
                <input
                  type="text"
                  value={deleteConfirmation}
                  onChange={(event) => setDeleteConfirmation(event.target.value)}
                  autoComplete="off"
                  spellCheck="false"
                  required
                />
              </label>
              {deleteError ? <p className="ux-delete-error" role="alert">{deleteError}</p> : null}
              <div className="ux-delete-actions">
                <button type="button" className="dri-btn dri-btn-ghost" onClick={closeDeletePanel} disabled={deleteBusy}>Cancelar</button>
                <button type="submit" className="dri-btn ux-danger-button" disabled={!canDeleteAccount}>
                  {deleteBusy ? 'Eliminando…' : 'Eliminar cuenta definitivamente'}
                </button>
              </div>
            </form>
          )}
        </section>
      </section>
    </main>
  )
}
