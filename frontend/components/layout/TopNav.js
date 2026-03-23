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
      <div className="top-nav-inner app-shell">
        <Link href="/" className="brand-mark top-brand">
          <span className="brand-dot" />
          <span className="top-brand-copy">
            <strong>{siteName}</strong>
            <small>TCG catalog platform</small>
          </span>
        </Link>

        <nav className="top-links" aria-label="Main navigation">
          <Link href="/" className="top-link">Home</Link>
          {primaryGames.map((game) => (
            <Link key={game.href} href={game.href} className="top-link">{game.label}</Link>
          ))}
        </nav>

        <div className="top-nav-actions">
          <Link href="/pokemon" className="secondary-btn top-nav-cta">Abrir Pokémon</Link>
        </div>
      </div>
    </header>
  )
}
