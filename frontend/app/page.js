import Link from 'next/link'
import TopNav from '../components/layout/TopNav'
import { GAME_CATALOG } from '../lib/catalog/games'

function GameCard({ game }) {
  return (
    <Link href={`/tcg/${game.slug}`} className="tcg-card panel" style={{ '--game-accent': game.accent }}>
      <div className="tcg-card-glow" aria-hidden="true" />
      <p className="tcg-card-eyebrow">{game.eyebrow}</p>
      <h3>{game.name}</h3>
      <p>{game.description}</p>
      <span className="tcg-card-link">Abrir explorer →</span>
    </Link>
  )
}

export default function HomePage() {
  return (
    <main>
      <TopNav />

      <section className="landing-shell landing-v2">
        <div className="landing-hero panel hero-stage">
          <div className="hero-copy">
            <p className="kicker">Don’tRipIt · TCG scoped explorers</p>
            <h1>Explora cada TCG con foco, velocidad y una vibra oscura tipo Instant Gaming.</h1>
            <p>
              Cambiamos la portada por una experiencia centrada en juegos concretos: entra a Pokémon, MTG,
              Yu-Gi-Oh!, ONE PIECE o Riftbound y busca cartas, prints y sets sin ruido multi-game.
            </p>
            <div className="landing-actions">
              <Link href="/tcg/pokemon" className="primary-btn">Entrar a Pokémon</Link>
              <Link href="/explorer" className="secondary-btn">Explorar todo</Link>
            </div>
          </div>

          <div className="hero-orbit" aria-hidden="true">
            <div className="hero-orbit-ring ring-a" />
            <div className="hero-orbit-ring ring-b" />
            <div className="hero-feature-card panel-soft">
              <span>Scoped search</span>
              <strong>Buscar solo dentro de un juego</strong>
            </div>
            <div className="hero-feature-card panel-soft alt">
              <span>Card detail</span>
              <strong>Variantes con miniaturas y etiquetas útiles</strong>
            </div>
          </div>
        </div>

        <div className="landing-strip panel-soft">
          <div>
            <p className="kicker">Catálogo principal</p>
            <h2>Selecciona tu juego y navega con contexto desde la primera pantalla.</h2>
          </div>
          <Link href="/explorer" className="back-link">Explorar todo el catálogo</Link>
        </div>

        <section className="tcg-grid-section">
          <div className="section-copy">
            <p className="kicker">TCGs incluidos</p>
            <h2>Exploradores dedicados por juego.</h2>
            <p>Cada acceso directo te lleva a una ruta `/tcg/[slug]` con búsqueda filtrada y resultados enfocados.</p>
          </div>
          <div className="tcg-grid">
            {GAME_CATALOG.map((game) => <GameCard key={game.slug} game={game} />)}
          </div>
        </section>
      </section>
    </main>
  )
}
