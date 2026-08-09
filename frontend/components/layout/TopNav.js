'use client'

import Link from 'next/link'
import { useState } from 'react'
import BrandMark from '../brand/BrandMark'

const shopUrl = 'https://shop.dontripit.com'

const navItems = [
  { href: '/#games', label: 'Juegos' },
  { href: '/#features', label: 'Qué puedes hacer' },
  { href: '/#news', label: 'Noticias' },
]

export default function TopNav() {
  const [open, setOpen] = useState(false)

  return (
    <header className="dri-nav">
      <div className="dri-nav-inner app-shell">
        <Link href="/" className="dri-nav-brand" aria-label="Don’tRipIt inicio" onClick={() => setOpen(false)}>
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
          <Link href="/login" className="dri-btn dri-btn-ghost">Entrar</Link>
          <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta</Link>
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
              <Link href="/login" className="dri-btn dri-btn-ghost" onClick={() => setOpen(false)}>Entrar</Link>
              <Link href="/register" className="dri-btn dri-btn-primary" onClick={() => setOpen(false)}>Crear cuenta</Link>
            </div>
          </div>
        </div>
      ) : null}
    </header>
  )
}
