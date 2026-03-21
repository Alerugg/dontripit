import Link from 'next/link'
import { homeGames } from './homeData'

function HomeGameCard({ game }) {
  return (
    <article className="home-game-card panel" style={{ '--game-accent': game.accent }}>
      <div className="home-game-card-glow" aria-hidden="true" />
      <p className="tcg-card-eyebrow">{game.eyebrow}</p>
      <h3>{game.name}</h3>
      <p>{game.description}</p>
      <div className="home-game-card-footer">
        <Link href={game.href} className="primary-btn">{game.cta}</Link>
        <Link href={game.secondaryHref} className="home-inline-link">Ver hub</Link>
      </div>
    </article>
  )
}

export default function HomeGameGrid() {
  return (
    <section className="home-section-stack">
      <div className="section-copy section-copy-wide home-section-heading">
        <p className="kicker">Catálogo por juego</p>
        <h2>Entradas claras al catálogo en una grid premium, pensada para respirar mejor y ocupar el ancho real.</h2>
        <p>
          La home ya no depende de un bloque central repetido: cada juego vive dentro de una retícula ancha con
          CTAs reales a hubs y explorers, preparada para añadir más universos sin perder claridad.
        </p>
      </div>

      <div className="home-game-grid">
        {homeGames.map((game) => <HomeGameCard key={game.slug} game={game} />)}
      </div>
    </section>
  )
}
