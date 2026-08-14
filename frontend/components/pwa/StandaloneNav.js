'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const ITEMS = [
  { href: '/', label: 'Inicio', icon: '⌂' },
  { href: '/search', label: 'Explorar', icon: '⌕' },
  { href: '/collection', label: 'Colección', icon: '▣' },
  { href: '/dashboard', label: 'Cuenta', icon: '○' },
]

const HIDDEN_PREFIXES = ['/login', '/register', '/forgot-password', '/reset-password']

export default function StandaloneNav() {
  const pathname = usePathname() || '/'
  if (HIDDEN_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return null

  return (
    <nav className="pwa-bottom-nav" aria-label="Navegación de la aplicación">
      {ITEMS.map((item) => {
        const active = item.href === '/'
          ? pathname === '/'
          : pathname === item.href || pathname.startsWith(`${item.href}/`)
        return (
          <Link key={item.href} href={item.href} className={active ? 'is-active' : ''} aria-current={active ? 'page' : undefined}>
            <span aria-hidden="true">{item.icon}</span>
            <small>{item.label}</small>
          </Link>
        )
      })}
    </nav>
  )
}
