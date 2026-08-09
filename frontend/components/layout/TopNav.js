'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import BrandMark from '../brand/BrandMark'

const shopUrl = 'https://shop.dontripit.com'

const publicNavItems = [
  { href: '/#games', label: 'Juegos' },
  { href: '/#features', label: 'Cómo funciona' },
  { href: '/#news', label: 'Novedades' },
]

export default function TopNav() {
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

  const navItems = user
    ? [
        { href: '/dashboard', label: 'Inicio' },
        { href: '/collection', label: 'Mi colección' },
        { href: '/wishlist', label: 'Wishlist' },
      ]
    : publicNavItems

  return (
    <header className="dri-nav">
      <div className="dri-nav-inner app-shell">
        <Link href={user ? '/dashboard' : '/'} className="dri-nav-brand" aria-label="Don’tRipIt inicio" onClick={() => setOpen(false)}>
          <BrandMark />
        </Link>

        <nav className="dri-nav-links" aria-label="Navegación principal">
          {navItems.map((item) => (
            <Link key={item.href} href={item.href} className="dri-nav-link">
              {item.label}
            </Link>
          ))}
          <a href={shopUrl} className="dri-nav-link">Tienda</a>
        </nav>

        <div className="dri-nav-actions">
          {authChecked && user ? (
            <>
              <Link href="/dashboard" className="dri-btn dri-btn-primary">{user.name?.split(' ')[0] || 'Mi cuenta'}</Link>
              <button type="button" className="dri-btn dri-btn-ghost" onClick={logout}>Salir</button>
            </>
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
            <a href={shopUrl}>Tienda</a>
            <div className="dri-mobile-menu-actions">
              {user ? (
                <>
                  <Link href="/dashboard" className="dri-btn dri-btn-primary" onClick={() => setOpen(false)}>Mi cuenta</Link>
                  <button type="button" className="dri-btn dri-btn-ghost" onClick={logout}>Salir</button>
                </>
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
