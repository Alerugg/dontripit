import Image from 'next/image'
import Link from 'next/link'
import TopNav from '../layout/TopNav'
import HomeSearch from './HomeSearch'
import { GAME_CATALOG } from '../../lib/catalog/games'

const GAME_LOGOS = {
  pokemon: '/games/pokemon/pokemon_logo.png',
  magic: '/games/magic/magic_logo.png',
  onepiece: '/games/onepiece/onepiece_logo.png',
  yugioh: '/games/yugioh/yugioh_logo.png',
}

const WORKSPACE_CARDS = [
  { label: 'Buscar', title: 'Encuentra una carta', copy: 'Nombre, número o set. Empieza por lo que recuerdas.' },
  { label: 'Seleccionar', title: 'Elige la versión física', copy: 'Set, rareza, idioma, acabado y variante permanecen separados.' },
  { label: 'Guardar', title: 'Colección o wishlist', copy: 'Añade exactamente la edición que tienes o la que quieres.' },
]

export default function PublicHome() {
  const activeGames = GAME_CATALOG.filter((game) => game.slug !== 'riftbound')

  return (
    <main className="canva-home">
      <TopNav />

      <section className="canva-workspace app-shell">
        <header className="canva-workspace-head">
          <div>
            <span className="canva-eyebrow">Catálogo TCG</span>
            <h1>Tu colección empieza por encontrar <em>la versión correcta.</em></h1>
          </div>
          <div className="canva-account-cta">
            <span>Tu espacio personal</span>
            <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta</Link>
          </div>
        </header>

        <div className="canva-search-shell" id="search">
          <div className="canva-search-label">
            <span>Buscar carta</span>
            <small>Nombre · número · set</small>
          </div>
          <HomeSearch />
        </div>

        <div className="canva-workspace-grid">
          <section className="canva-results-pane" id="games">
            <div className="canva-pane-title">
              <div>
                <span className="canva-eyebrow">Resultados</span>
                <h2>Selecciona los detalles de tu carta</h2>
              </div>
              <Link href="/search" className="canva-text-link">Búsqueda avanzada →</Link>
            </div>

            <div className="canva-card-row">
              {activeGames.map((game) => (
                <Link key={game.slug} href={`/games/${game.slug}`} className="canva-tcg-card" style={{ '--game-accent': game.accent }}>
                  <div className="canva-tcg-art">
                    <Image src={GAME_LOGOS[game.slug]} alt={game.name} width={260} height={100} sizes="180px" />
                  </div>
                  <div className="canva-tcg-copy">
                    <span>Catálogo activo</span>
                    <strong>{game.name}</strong>
                    <small>Explorar cartas y sets</small>
                  </div>
                </Link>
              ))}
              <Link href="/search" className="canva-tcg-card canva-more-card">
                <div className="canva-more-mark">⌕</div>
                <div className="canva-tcg-copy">
                  <span>Todos los juegos</span>
                  <strong>Buscar catálogo</strong>
                  <small>Encuentra una versión exacta</small>
                </div>
              </Link>
            </div>

            <div className="canva-workflow-strip" id="how-it-works">
              {WORKSPACE_CARDS.map((item, index) => (
                <article key={item.label} className="canva-workflow-item">
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <div>
                    <small>{item.label}</small>
                    <strong>{item.title}</strong>
                    <p>{item.copy}</p>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <aside className="canva-collection-pane" id="releases">
            <div className="canva-tabs" aria-label="Vista de colección">
              <span className="is-active">Colección</span>
              <span>Wishlist</span>
              <span>Progreso de set</span>
            </div>

            <div className="canva-side-hero">
              <span className="canva-eyebrow">Tus cartas</span>
              <h2>Todo lo que guardas, por edición exacta.</h2>
              <p>Tu colección, wishlist y progreso por set usan la misma identidad física que ves en el catálogo.</p>
            </div>

            <div className="canva-side-list">
              <div><span>Identidad</span><strong>Versión exacta</strong><small>Set · rareza · idioma · variante</small></div>
              <div><span>Mercado</span><strong>Precio verificable</strong><small>Fuente y fecha visibles</small></div>
              <div><span>Portfolio</span><strong>Sin estimaciones falsas</strong><small>Solo se valora lo que tiene precio seguro</small></div>
            </div>

            <Link href="/register" className="dri-btn dri-btn-primary canva-side-button">Empezar mi colección →</Link>
          </aside>
        </div>
      </section>
    </main>
  )
}
