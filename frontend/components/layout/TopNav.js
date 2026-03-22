import Link from 'next/link'

const siteName = process.env.NEXT_PUBLIC_SITE_NAME || 'Don’tRipIt'

const primaryGames = [
  { href: '/pokemon', label: 'Pokémon' },
  { href: '/magic', label: 'Magic' },
  { href: '/onepiece', label: 'One Piece' },
  { href: '/yugioh', label: 'Yu-Gi-Oh!' },
  { href: '/riftbound', label: 'Riftbound' },
]

export default function TopNav() {
  return (
    <header className="top-nav">
      <div className="top-nav-inner">
        <Link href="/" className="brand-mark">
          <span className="brand-dot" />
          <span>{siteName}</span>
        </Link>

        <nav className="top-links" aria-label="Main navigation">
          <Link href="/" className="top-link">Home</Link>
          {primaryGames.map((game) => (
            <Link key={game.href} href={game.href} className="top-link">{game.label}</Link>
          ))}
        </nav>

        <div className="top-nav-actions">
          <Link href="/pokemon" className="secondary-btn">Abrir Pokémon</Link>
          <Link href="/admin/api-console" className="admin-link">Admin Console</Link>
        </div>
      </div>
    </header>
  )
}
