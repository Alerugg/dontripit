import Link from 'next/link'
import { homeGames } from './homeData'

function HomeGameCard({ game }) {
  return (
    <article className="home-game-card home-panel-soft" style={{ '--game-glow': game.glow }}>
      <div className="home-game-card-glow" aria-hidden="true" />
      <span className="home-panel-tag">{game.label}</span>
      <p className="home-game-name">{game.name}</p>
      <p className="home-game-eyebrow">{game.eyebrow}</p>
      <p className="home-game-description">{game.description}</p>
      <div className="home-game-actions">
        <Link href={game.href} className="secondary-btn">{game.cta}</Link>
        <Link href={game.secondaryHref} className="home-inline-link">Ver ruta relacionada</Link>
      </div>
    </article>
  )
}

export default function HomeGamesStrip() {
  return (
    <section className="home-section home-games-strip">
      <div className="home-section-heading">
        <p className="home-kicker">Juegos disponibles</p>
        <h2>Hubs premium para cada TCG con accesos claros a catálogo, sets y explorers activos.</h2>
      </div>

      <div className="home-grid-3 home-games-grid">
        {homeGames.map((game) => <HomeGameCard key={game.slug} game={game} />)}
      </div>
    </section>
  )
}
