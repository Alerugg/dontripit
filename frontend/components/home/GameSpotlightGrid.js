import Link from 'next/link'
import { GAME_CATALOG } from '../../lib/catalog/games'
import { getGameExplorerHref, getGameHref } from '../../lib/catalog/routes'

function GameSpotlightCard({ game }) {
  return (
    <article className="tcg-card panel" style={{ '--game-accent': game.accent }}>
      <div className="tcg-card-glow" aria-hidden="true" />
      <p className="tcg-card-eyebrow">{game.eyebrow}</p>
      <h3>{game.name}</h3>
      <p>{game.description}</p>
      <div className="tcg-card-actions">
        <Link href={getGameHref(game.slug)} className="secondary-btn">Hub del juego</Link>
        <Link href={getGameExplorerHref(game.slug)} className="primary-btn">Entrar</Link>
      </div>
    </article>
  )
}

export default function GameSpotlightGrid() {
  return (
    <section className="tcg-grid-section">
      <div className="section-copy section-copy-wide">
        <p className="kicker">TCGs disponibles</p>
        <h2>El usuario elige primero su juego y a partir de ahí todo gana contexto.</h2>
        <p>
          Cada TCG tiene una base navegable propia para escalar catálogo, marketplace, idiomas, colecciones y
          resultados específicos sin arrastrar un explorador multi-game pesado.
        </p>
      </div>

      <div className="tcg-grid">
        {GAME_CATALOG.map((game) => <GameSpotlightCard key={game.slug} game={game} />)}
      </div>
    </section>
  )
}
