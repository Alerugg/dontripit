import Link from 'next/link'
import { getGameExplorerHref } from '../../lib/catalog/routes'
import { homeHeroHighlights, homeMockupColumns } from './homeData'

export default function HomeHero() {
  return (
    <section className="home-hero-v2">
      <div className="home-hero-copy-v2">
        <span className="home-badge">HOME V2 REAL</span>
        <p className="kicker">Don’tRipIt · premium TCG catalog platform</p>
        <h1>Una home cinematográfica para descubrir TCGs como producto serio, escalable y future-ready.</h1>
        <p className="home-hero-lead">
          Don’tRipIt pasa de landing simple a front door de plataforma: catálogo por juego, narrativa de producto,
          señales de confianza y una base visual lista para pricing, sellers y marketplace.
        </p>

        <div className="landing-actions home-hero-actions">
          <Link href={getGameExplorerHref('pokemon')} className="primary-btn">Entrar al explorer de Pokémon</Link>
          <Link href="/games/mtg" className="secondary-btn">Explorar hubs por juego</Link>
        </div>

        <div className="home-hero-highlights">
          {homeHeroHighlights.map((item) => (
            <div key={item} className="home-highlight-item panel-soft">
              <span className="home-highlight-dot" aria-hidden="true" />
              <p>{item}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="home-hero-visual-v2 panel" aria-hidden="true">
        <div className="home-hero-glow home-hero-glow-a" />
        <div className="home-hero-glow home-hero-glow-b" />

        <div className="home-product-frame">
          <div className="home-product-topbar">
            <div className="home-window-dots">
              <span />
              <span />
              <span />
            </div>
            <p>Catalog orchestration · multi-TCG navigation</p>
          </div>

          <div className="home-product-layout">
            <aside className="home-product-sidebar">
              <p className="home-panel-label">Workspace</p>
              <strong>Catalog Console</strong>
              <div className="home-sidebar-list">
                {homeMockupColumns[0].items.map((item) => <span key={item}>{item}</span>)}
              </div>
            </aside>

            <div className="home-product-main">
              <div className="home-product-card panel-soft">
                <p className="home-panel-label">Portfolio coverage</p>
                <strong>Pokémon · MTG · ONE PIECE · Riftbound</strong>
                <div className="home-signal-row">
                  <span>Explorer</span>
                  <span>Set landing</span>
                  <span>Print detail</span>
                </div>
              </div>

              <div className="home-product-grid">
                {homeMockupColumns[1].items.map((item, index) => (
                  <div key={item} className="home-mini-panel">
                    <p>0{index + 1}</p>
                    <strong>{item}</strong>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="home-floating-note home-floating-note-top panel-soft">
          <span>Product frame</span>
          <strong>Home + catalog + marketplace roadmap</strong>
        </div>
        <div className="home-floating-note home-floating-note-bottom panel-soft">
          <span>Trust signal</span>
          <strong>Rutas activas y escalado visual sin romper explorers</strong>
        </div>
      </div>
    </section>
  )
}
