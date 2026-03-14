'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const NAV_ITEMS = [
  { href: '/', label: 'Explorer' },
  { href: '/api-console', label: 'API Console' },
]

export default function AppShell({ children }) {
  const pathname = usePathname()

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <Link href="/" className="brand">Arcana Index</Link>
          <span className="brand-subtitle">TCG catalog · collection-ready</span>
        </div>
        <nav className="top-nav">
          {NAV_ITEMS.map((item) => (
            <Link key={item.href} href={item.href} className={`nav-link ${pathname === item.href ? 'active' : ''}`}>
              {item.label}
            </Link>
          ))}
          <span className="nav-pill">Wishlist · Binder · Marketplace (soon)</span>
        </nav>
      </header>
      {children}
    </div>
  )
}
