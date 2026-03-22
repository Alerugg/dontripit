import Link from 'next/link'
import TopNav from '../components/layout/TopNav'
import HomeMetrics from '../components/home/HomeMetrics'
import { GAME_CATALOG } from '../lib/catalog/games'

const metrics = [
  {
    value: '1 carta',
    label: 'por resultado principal',
    detail: 'La búsqueda prioriza cartas y deja las variantes dentro del detalle.',
  },
  {
    value: '5 TCGs',
    label: 'rutas dedicadas',
    detail: 'Cada juego abre su propio explorador, colecciones, placeholders de torneos y noticias.',
  },
  {
    value: 'Docker-only',
    label: 'flujo consistente',
    detail: 'La experiencia se diseña para un entorno operativo unificado y listo para crecer.',
  },
]

const valueProps = [
  {
    title: 'Marketplace-ready',
    description: 'Base visual pensada para conectar pricing, sellers, wishlist y colección sin rehacer la navegación.',
  },
  {
    title: 'Set → Carta → Variantes',
    description: 'Jerarquía clara para mantener la búsqueda ligera y mover el detalle profundo a la ficha correcta.',
  },
  {
    title: 'Búsqueda rápida',
    description: 'Autocomplete visual, resultados deduplicados y estado persistente al volver atrás.',
  },
]

export default function HomePage() {
  return (
    <main className="home-page">
      <TopNav />
      <div className="page-shell home-shell">
        <section className="hero panel">
          <div className="hero-copy">
            <p className="eyebrow">Catálogo premium para TCGs</p>
            <h1>Don’tRipIt</h1>
            <h2>Explora cartas y sellado por TCG. Una carta por resultado. Variantes dentro.</h2>
            <p className="hero-text">
              Una home clara, rápida y orientada a juego. Entra por tu TCG, busca sin duplicados y conserva el estado al navegar.
            </p>
            <div className="hero-actions">
              <Link href="/pokemon" className="primary-btn">Explorar Pokémon</Link>
              <Link href="#tcg-grid" className="secondary-btn">Ver todos los TCGs</Link>
            </div>
          </div>

          <div className="hero-visual" aria-hidden="true">
            <div className="hero-card hero-card-a panel-soft">
              <span>Carta</span>
              <strong>Moltres</strong>
              <small>Variantes, sets y metadata limpia</small>
            </div>
            <div className="hero-card hero-card-b panel-soft">
              <span>Sellado</span>
              <strong>Booster boxes</strong>
              <small>Preparado para categorías y stock futuro</small>
            </div>
            <div className="hero-card hero-card-c panel-soft">
              <span>Explorador dedicado</span>
              <strong>Pokémon</strong>
              <small>Búsqueda, colecciones, noticias y torneos</small>
            </div>
          </div>
        </section>

        <HomeMetrics metrics={metrics} />

        <section id="tcg-grid" className="tcg-grid-section">
          <div className="section-heading">
            <p className="eyebrow">Rutas directas por juego</p>
            <h2>Elige tu TCG y entra a su explorador dedicado.</h2>
          </div>
          <div className="tcg-grid">
            {GAME_CATALOG.map((game) => (
              <Link key={game.slug} href={`/${game.slug}`} className="tcg-tile panel-soft" style={{ '--game-accent': game.accent }}>
                <p className="eyebrow">{game.eyebrow}</p>
                <h3>{game.name}</h3>
                <p>{game.description}</p>
                <span className="tile-link">Abrir {game.name}</span>
              </Link>
            ))}
          </div>
        </section>

        <section className="value-props">
          {valueProps.map((item) => (
            <article key={item.title} className="value-card panel-soft">
              <h3>{item.title}</h3>
              <p>{item.description}</p>
            </article>
          ))}
        </section>
      </div>

      <footer className="site-footer">
        <div className="page-shell site-footer-inner">
          <p>Don’tRipIt · Catálogo TCG con rutas dedicadas, UX consistente y base lista para marketplace.</p>
        </div>
      </footer>
    </main>
  )
}
