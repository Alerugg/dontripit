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
    <header className="top-nav top-nav-v2">
      <div className="top-nav-inner app-shell">
        <Link href="/" className="top-brand-logo-wrap" aria-label={siteName}>
          <img
            src="/branding/dontripit_logo.png"
            alt={siteName}
            className="top-brand-logo"
          />
        </Link>

        <nav className="top-links top-links-v2" aria-label="Main navigation">
          {primaryGames.map((game) => (
            <Link key={game.href} href={game.href} className="top-link top-link-v2">
              {game.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  )
}