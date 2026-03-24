import Link from 'next/link'
import { GAME_CATALOG } from '../../lib/catalog/games'
import { getGameExplorerHref } from '../../lib/catalog/routes'

const GAME_IMAGES = {
  pokemon: '/games/pokemon/pokemon_logo.png',
  magic: '/games/magic/magic_logo.png',
  onepiece: '/games/onepiece/onepiece_logo.png',
  yugioh: '/games/yugioh/yugioh_logo.png',
  riftbound: '/games/riftbound/riftbound_logo.png',
}

function GameSpotlightCard({ game }) {
  const imageSrc = GAME_IMAGES[game.slug]

  return (
    <Link
      href={getGameExplorerHref(game.slug)}
      className="tcg-entry-card tcg-entry-card-logo-only"
      style={{ '--game-accent': game.accent }}
      aria-label={`Entrar al explorer de ${game.name}`}
    >
      <div className="tcg-entry-logo-wrap tcg-entry-logo-wrap-only">
        {imageSrc ? (
          <img src={imageSrc} alt={game.name} className="tcg-entry-logo tcg-entry-logo-only" />
        ) : (
          <div className="tcg-entry-logo-fallback">{game.name}</div>
        )}
      </div>
    </Link>
  )
}

export default function GameSpotlightGrid() {
  return (
    <section className="tcg-grid-section tcg-grid-section-v2">
      <div className="section-copy section-copy-wide">
        <p className="kicker">Elige tu TCG</p>
        <h2>Empieza por el juego y entra directo a su explorer dedicado.</h2>
      </div>

      <div className="tcg-grid tcg-grid-v2">
        {GAME_CATALOG.map((game) => (
          <GameSpotlightCard key={game.slug} game={game} />
        ))}
      </div>
    </section>
  )
}