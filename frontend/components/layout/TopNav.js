'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import BrandMark from '../brand/BrandMark'

const shopUrl = 'https://shop.dontripit.com'

const publicNavItems = [
  { href: '/explorer', label: 'Explorador' },
  { href: '/#games', label: 'Juegos' },
  { href: '/#how-it-works', label: 'Cómo funciona' },
]

const memberNavItems = [
  { href: '/explorer', label: 'Explorador' },
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/collection', label: 'Mi colección' },
  { href: '/wishlist', label: 'Wishlist' },
]

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

  const navItems = user ? memberNavItems : publicNavItems

  function isActive(item) {
    const path = item.href.split('#')[0]
    if (path === '/') return pathname === '/'
    return pathname === path || pathname.startsWith(`${path}/`)
  }

  return (
    <header className="dri-nav">
      <div className="dri-nav-inner app-shell">
        <Link href={user ? '/dashboard' : '/'} className="dri-nav-brand" aria-label="Don’tRipIt inicio" onClick={() => setOpen(false)}>
          <BrandMark />
        </Link>

        <nav className="dri-nav-links" aria-label="Navegación principal">
          {navItems.map((item) => (
            <Link key={item.href} href={item.href} className={`dri-nav-link ${isActive(item) ? 'is-active' : ''}`}>
              {item.label}
            </Link>
          ))}
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
          onClick={() => setOpen((current) => !current)}
        >
          <span />
          <span />
        </button>
      </div>

      {open ? (
        <div className="dri-mobile-menu">
          <div className="app-shell dri-mobile-menu-inner">
            {navItems.map((item) => (
              <Link key={item.href} href={item.href} onClick={() => setOpen(false)}>{item.label}</Link>
            ))}
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
