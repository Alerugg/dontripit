'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import BrandMark from '../brand/BrandMark'

const shopUrl = 'https://shop.dontripit.com'

const games = [
  ['pokemon', 'Pokémon'],
  ['onepiece', 'One Piece'],
  ['magic', 'Magic'],
  ['yugioh', 'Yu-Gi-Oh'],
  ['riftbound', 'Riftbound'],
]

function Menu({ label, children }) {
  return (
    <details className="dri-nav-menu">
      <summary>{label}</summary>
      <div className="dri-nav-menu-popover">{children}</div>
    </details>
  )
}

export default function TopNav() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/api/auth/me', { cache: 'no-store' })
      .then(async (response) => response.ok ? response.json() : null)
      .then((payload) => { if (!cancelled) setUser(payload?.user || null) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setAuthChecked(true) })
    return () => { cancelled = true }
  }, [])

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' }).catch(() => null)
    setUser(null)
    setOpen(false)
    window.location.assign('/')
  }

  function isActive(path) {
    return pathname === path || pathname.startsWith(`${path}/`)
  }

  return (
    <header className="dri-nav">
      <div className="dri-nav-inner app-shell">
        <Link href={user ? '/dashboard' : '/'} className="dri-nav-brand" aria-label="Don’tRipIt inicio" onClick={() => setOpen(false)}>
          <BrandMark />
        </Link>

        <nav className="dri-nav-links" aria-label="Navegación principal">
          <Link href="/explorer" className={`dri-nav-link ${isActive('/explorer') ? 'is-active' : ''}`}>Explorar</Link>
          <Menu label="Juegos">
            {games.map(([slug, label]) => (
              <Link key={slug} href={`/games/${slug}`}>
                <span>{label}</span>
                <small>Hub</small>
              </Link>
            ))}
          </Menu>
          {user ? <Link href="/dashboard" className={`dri-nav-link ${isActive('/dashboard') ? 'is-active' : ''}`}>Panel</Link> : null}
          {user ? (
            <Menu label="Mi cartera">
              <Link href="/collection"><span>Colección</span><small>Portfolio</small></Link>
              <Link href="/wishlist"><span>Wishlist</span><small>Radar</small></Link>
              <Link href="/dashboard"><span>Dashboard</span><small>Cuenta</small></Link>
            </Menu>
          ) : null}
          <a href={shopUrl} className="dri-nav-link" target="_blank" rel="noopener noreferrer">Tienda ↗</a>
        </nav>

        <div className="dri-nav-actions">
          {authChecked && user ? (
            <button type="button" className="dri-btn dri-btn-ghost" onClick={logout}>Salir</button>
          ) : authChecked ? (
            <>
              <Link href="/login" className="dri-btn dri-btn-ghost">Entrar</Link>
              <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta</Link>
            </>
          ) : null}
        </div>

        <button
          type="button"
          className={`dri-menu-toggle ${open ? 'is-open' : ''}`}
          aria-label={open ? 'Cerrar menú' : 'Abrir menú'}
          aria-expanded={open}
          aria-controls="dri-mobile-nav"
          onClick={() => setOpen((current) => !current)}
        >
          <span />
          <span />
        </button>
      </div>

      {open ? (
        <div className="dri-mobile-menu" id="dri-mobile-nav">
          <div className="app-shell dri-mobile-menu-inner">
            <Link href="/explorer" onClick={() => setOpen(false)}>Explorar</Link>
            {games.map(([slug, label]) => <Link key={slug} href={`/games/${slug}`} onClick={() => setOpen(false)}>{label}</Link>)}
            {user ? <Link href="/dashboard" onClick={() => setOpen(false)}>Panel</Link> : null}
            {user ? <Link href="/collection" onClick={() => setOpen(false)}>Colección</Link> : null}
            {user ? <Link href="/wishlist" onClick={() => setOpen(false)}>Wishlist</Link> : null}
            <a href={shopUrl} target="_blank" rel="noopener noreferrer">Tienda ↗</a>
            <div className="dri-mobile-menu-actions">
              {user ? (
                <button type="button" className="dri-btn dri-btn-ghost" onClick={logout}>Salir</button>
              ) : (
                <>
                  <Link href="/login" className="dri-btn dri-btn-ghost" onClick={() => setOpen(false)}>Entrar</Link>
                  <Link href="/register" className="dri-btn dri-btn-primary" onClick={() => setOpen(false)}>Crear cuenta</Link>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </header>
  )
}
