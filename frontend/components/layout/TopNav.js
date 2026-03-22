import Link from 'next/link'

const siteName = process.env.NEXT_PUBLIC_SITE_NAME || 'Don’tRipIt'

export default function TopNav() {
  return (
    <header className="top-nav top-nav-premium">
      <div className="top-nav-inner">
        <Link href="/" className="brand-mark">
          <span className="brand-dot" />
          <span>{siteName}</span>
        </Link>

        <nav className="top-links" aria-label="Main navigation">
          <Link href="/" className="top-link">Home</Link>
          <Link href="/games/pokemon" className="top-link">Juegos</Link>
          <Link href="/explorer" className="top-link">Explorar todo</Link>
          <span className="top-link disabled">Colección</span>
          <span className="top-link disabled">Wishlist</span>
        </nav>

        <div className="top-nav-actions">
          <Link href="/games/pokemon/explorer" className="secondary-btn">Abrir explorer</Link>
          <Link href="/admin/api-console" className="admin-link">Admin Console</Link>
        </div>
      </div>
    </header>
  )
}
