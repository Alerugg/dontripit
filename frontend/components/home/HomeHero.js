import Link from 'next/link'
import { getGameExplorerHref } from '../../lib/catalog/routes'
import { homeHeroLayers, homeHeroStats } from './homeData'

export default function HomeHero() {
  return (
    <section className="home-section home-hero">
      <div className="home-hero-panel home-panel">
        <div className="home-hero-copy">
          <span className="sr-only">HOME V2 REAL</span>
          <p className="home-kicker">Don’tRipIt · premium TCG platform</p>
          <h1>
            Descubre TCGs, explora sets y compara variantes con una interfaz creada para
            <span> coleccionistas serios.</span>
          </h1>
          <p className="home-hero-lead">
            Don’tRipIt organiza catálogo, hubs y explorers con una presencia oscura, editorial y lista para crecer
            hacia wishlist, colección, pricing y marketplace sin perder claridad.
          </p>

          <div className="home-hero-actions">
            <Link href={getGameExplorerHref('pokemon')} className="primary-btn">Explorar Pokémon</Link>
            <Link href="/games/pokemon" className="secondary-btn">Ver hubs por juego</Link>
          </div>

          <div className="home-hero-stats" aria-label="Capacidades principales de la plataforma">
            {homeHeroStats.map((item) => (
              <div key={item} className="home-pill-panel">
                <span className="home-pill-dot" aria-hidden="true" />
                <p>{item}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="home-hero-visual" aria-hidden="true">
          <div className="home-glow-orb home-glow-gold" />
          <div className="home-glow-orb home-glow-blue" />
          <div className="home-glow-orb home-glow-violet" />

          <div className="home-dashboard-card home-panel-strong">
            <div className="home-dashboard-head">
              <span className="home-panel-tag">Explorer surface</span>
              <strong>Collector command layer</strong>
            </div>

            <div className="home-dashboard-search">
              <span className="home-dashboard-label">Search across cards, sets and prints</span>
              <div className="home-dashboard-input">Search “Pikachu”, “Base Set”, “foil alt art”</div>
            </div>

            <div className="home-dashboard-stack">
              {homeHeroLayers.map((item) => (
                <article key={item.title} className="home-floating-card">
                  <span className="home-panel-tag">{item.eyebrow}</span>
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </article>
              ))}
            </div>
          </div>

          <div className="home-mini-float home-mini-float-top home-panel-soft">
            <span className="home-panel-tag">Navigation</span>
            <strong>Entrada por TCG con hubs dedicados</strong>
          </div>

          <div className="home-mini-float home-mini-float-bottom home-panel-soft">
            <span className="home-panel-tag">Readiness</span>
            <strong>Wishlist, colección y pricing en la misma base</strong>
          </div>
        </div>
      </div>
    </section>
  )
}
