'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import TopNav from '../layout/TopNav'
import FallbackImage from '../common/FallbackImage'
import { GAME_CATALOG } from '../../lib/catalog/games'
import './DashboardPage.css'

function GameCard({ game }) {
  const soon = game.slug === 'riftbound'
  return (
    <Link
      href={soon ? '#' : `/games/${game.slug}`}
      className={`dashboard-game-card ${soon ? 'is-soon' : ''}`}
      style={{ '--game-accent': game.accent }}
      aria-disabled={soon}
    >
      <span className="dri-kicker">{soon ? 'Próximamente' : game.eyebrow}</span>
      <h3>{game.name}</h3>
      <p>{soon ? 'Estamos esperando el acceso de producción adecuado antes de abrir el catálogo.' : 'Busca por nombre, número, set y filtros propios del juego.'}</p>
      <strong>{soon ? 'En preparación' : 'Abrir catálogo →'}</strong>
    </Link>
  )
}

export default function DashboardPage() {
  const [user, setUser] = useState(null)
  const [collection, setCollection] = useState({ items: [], count: 0, known_value_eur: 0 })
  const [wishlist, setWishlist] = useState({ items: [], count: 0 })
  const [loading, setLoading] = useState(true)

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
          setCollection(own.items ? own : { items: [], count: 0, known_value_eur: 0 })
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

  return (
    <main>
      <TopNav />
      <section className="dashboard-shell">
        <div className="dashboard-hero">
          <section className="dashboard-welcome">
            <span className="dri-kicker">Tu Don’tRipIt</span>
            <h1>{loading ? 'Preparando tu colección…' : `Hola, ${user?.name?.split(' ')[0] || 'coleccionista'}.`}</h1>
            <p>Busca rápido cuando quieras una carta. Afina cuando necesites una edición concreta. Y guarda exactamente la versión física que tienes o quieres.</p>
            <div className="detail-actions">
              <Link href="/games/onepiece" className="dri-btn dri-btn-primary">Buscar cartas</Link>
              <Link href="/collection" className="dri-btn dri-btn-ghost">Mi colección</Link>
            </div>
          </section>

          <aside className="dashboard-summary-panel">
            <div className="dashboard-metric"><span>Ediciones</span><strong>{collection.count || 0}</strong></div>
            <div className="dashboard-metric"><span>Cartas físicas</span><strong>{pieces}</strong></div>
            <div className="dashboard-metric"><span>Wishlist</span><strong>{wishlist.count || 0}</strong></div>
            <div className="dashboard-metric"><span>Valor conocido*</span><strong>{new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(collection.known_value_eur || 0)}</strong></div>
          </aside>
        </div>

        <section className="dashboard-section">
          <div className="dashboard-section-head">
            <div><span className="dri-kicker">Elige tu juego</span><h2>¿Qué quieres buscar hoy?</h2></div>
          </div>
          <div className="dashboard-game-grid">
            {GAME_CATALOG.map((game) => <GameCard key={game.slug} game={game} />)}
          </div>
        </section>

        {recent.length ? (
          <section className="dashboard-section">
            <div className="dashboard-section-head">
              <div><span className="dri-kicker">Recientes</span><h2>Lo último que guardaste</h2></div>
              <Link href="/collection" className="home-inline-link">Ver toda mi colección →</Link>
            </div>
            <div className="dashboard-recent-grid">
              {recent.map((item) => (
                <Link key={item.id} href={`/prints/${item.print.id}`} className="dashboard-recent-card">
                  <div className="dashboard-recent-image">
                    <FallbackImage src={item.print.image_url} alt={item.print.card_name} className="detail-image" placeholderClassName="image-fallback" label={item.print.game} />
                  </div>
                  <strong>{item.print.card_name}</strong>
                  <small>{[item.print.set_code, item.print.collector_number].filter(Boolean).join(' · ')}</small>
                </Link>
              ))}
            </div>
          </section>
        ) : null}

        <p className="detail-meta" style={{ marginTop: 28 }}>*El valor conocido solo utiliza precios con fuente registrada; Don’tRipIt no inventa precios para completar huecos.</p>
      </section>
    </main>
  )
}
