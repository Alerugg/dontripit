import Link from 'next/link'

const siteName = process.env.NEXT_PUBLIC_SITE_NAME || 'TCG Nexus'

export default function TopNav() {
  return (
    <header className="top-nav">
      <div className="top-nav-inner">
        <Link href="/" className="brand-mark">
          <span className="brand-dot" />
          <span>{siteName}</span>
        </Link>

        <nav className="top-links">
          <Link href="/" className="top-link">Explorer</Link>
          <span className="top-link disabled">Colección</span>
          <span className="top-link disabled">Wishlist</span>
          <span className="top-link disabled">Marketplace</span>
        </nav>
      </div>
    </header>
  )
}
