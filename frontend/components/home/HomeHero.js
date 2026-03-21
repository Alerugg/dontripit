import Link from 'next/link'
import { getGameExplorerHref } from '../../lib/catalog/routes'

export default function HomeHero() {
  return (
    <section className="panel home-hero">
      <div className="home-hero-copy">
        <p className="kicker">Don’tRipIt · catálogo premium para coleccionistas</p>
        <h1>Una home real en React para descubrir TCGs, explorar sets y comparar variantes sin fricción.</h1>
        <p>
          Replanteamos la portada como base de producto: visual oscura, modular y lista para evolucionar hacia
          catálogo + marketplace sin depender de una landing HTML separada.
        </p>
        <div className="landing-actions">
          <Link href={getGameExplorerHref('pokemon')} className="primary-btn">Explorar Pokémon</Link>
          <Link href="/games/mtg" className="secondary-btn">Ver estructura por juego</Link>
        </div>
        <div className="hero-stats-grid">
          <div className="panel-soft hero-stat-card">
            <strong>Entrada por TCG</strong>
            <span>Menos ruido, búsquedas más rápidas y filtros realmente útiles.</span>
          </div>
          <div className="panel-soft hero-stat-card">
            <strong>Sets + variantes</strong>
            <span>Jerarquía pensada para carta, colección, print e idiomas.</span>
          </div>
        </div>
      </div>

      <div className="home-hero-visual" aria-hidden="true">
        <div className="hero-energy hero-energy-a" />
        <div className="hero-energy hero-energy-b" />
        <div className="hero-energy hero-energy-c" />
        <div className="hero-showcase-card panel-soft showcase-primary">
          <span>Explorer UX</span>
          <strong>Autocompletado con miniaturas y CTA siempre visible</strong>
        </div>
        <div className="hero-showcase-card panel-soft showcase-secondary">
          <span>Card detail</span>
          <strong>Variantes claras, sets enlazados y datos clave ordenados</strong>
        </div>
        <div className="hero-showcase-card panel-soft showcase-tertiary">
          <span>Marketplace-ready</span>
          <strong>Bloques reutilizables para catálogo, stock y pricing futuro</strong>
        </div>
      </div>
    </section>
  )
}
