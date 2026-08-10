import Image from 'next/image'
import Link from 'next/link'
import TopNav from '../layout/TopNav'
import SiteFooter from '../layout/SiteFooter'
import HomeSearch from './HomeSearch'
import { GAME_CATALOG } from '../../lib/catalog/games'

const GAME_LOGOS = {
  pokemon: '/games/pokemon/pokemon_logo.png',
  magic: '/games/magic/magic_logo.png',
  onepiece: '/games/onepiece/onepiece_logo.png',
  yugioh: '/games/yugioh/yugioh_logo.png',
  riftbound: '/games/riftbound/riftbound_logo.png',
}

const GAME_NOTES = {
  pokemon: 'Cartas, sets y variantes',
  magic: 'Prints, finishes e idiomas',
  onepiece: 'Leaders, promos y parallels',
  yugioh: 'Ediciones, rarezas y códigos',
  riftbound: 'Catálogo en preparación',
}

export default function PublicHome() {
  return (
    <main className="v4-site">
      <TopNav />

      <section className="v4-hero app-shell">
        <div className="v4-hero-copy">
          <span className="v4-overline"><i /> El catálogo para coleccionistas</span>
          <h1>Encuentra la carta.<br /><em>Controla la colección.</em></h1>
          <p>Busca por nombre, número o set. Don’tRipIt te lleva de la carta a la edición física exacta.</p>
          <HomeSearch />
          <div className="v4-hero-links">
            <Link href="/register" className="v4-button v4-button-primary">Crear cuenta gratis</Link>
            <a href="#games" className="v4-text-link">Explorar juegos <span>↓</span></a>
          </div>
          <ul className="v4-proof-list" aria-label="Funciones principales">
            <li><span>✓</span> Búsqueda precisa</li>
            <li><span>✓</span> Colección y wishlist</li>
            <li><span>✓</span> Precios con fuente</li>
          </ul>
        </div>

        <div className="v4-hero-visual" aria-label="Juegos disponibles en Don’tRipIt">
          <div className="v4-orbit v4-orbit-one" />
          <div className="v4-orbit v4-orbit-two" />
          <div className="v4-card-bundle">
            <Image
              src="/branding/tcg_bundle.png"
              alt="Selección de cartas coleccionables"
              width={1445}
              height={900}
              sizes="(max-width: 900px) 92vw, 48vw"
              priority
            />
          </div>
          <div className="v4-float-card v4-float-card-search">
            <span>Resultado exacto</span>
            <strong>Monkey D. Luffy</strong>
            <small>OP05-119 · 14 versiones</small>
          </div>
          <div className="v4-float-card v4-float-card-price">
            <span>Valor conservador</span>
            <strong>€42,80</strong>
            <small>Cardmarket · EUR</small>
          </div>
        </div>
      </section>

      <section className="v4-signal-bar">
        <div className="app-shell">
          <span>UNA CUENTA</span><i />
          <span>CUATRO CATÁLOGOS ACTIVOS</span><i />
          <span>IDENTIDAD FÍSICA EXACTA</span><i />
          <span>FUENTES VERIFICABLES</span>
        </div>
      </section>

      <section id="games" className="v4-section app-shell">
        <header className="v4-section-heading">
          <div>
            <span className="v4-overline"><i /> Catálogos</span>
            <h2>Tu juego. La misma claridad.</h2>
          </div>
          <p>Cada TCG conserva sus filtros, rarezas y reglas.</p>
        </header>

        <div className="v4-game-grid">
          {GAME_CATALOG.map((game) => {
            const soon = game.slug === 'riftbound'
            return (
              <Link
                key={game.slug}
                href={soon ? '/games/riftbound' : `/games/${game.slug}`}
                className={`v4-game-card v4-game-${game.slug} ${soon ? 'is-soon' : ''}`}
                style={{ '--game-accent': game.accent }}
              >
                <span className="v4-game-state">{soon ? 'Próximamente' : 'Explorar'}</span>
                <div className="v4-game-logo">
                  <Image src={GAME_LOGOS[game.slug]} alt={game.name} width={280} height={100} sizes="220px" />
                </div>
                <p>{GAME_NOTES[game.slug]}</p>
                <b aria-hidden="true">↗</b>
              </Link>
            )
          })}
        </div>
      </section>

      <section id="features" className="v4-section app-shell">
        <div className="v4-bento">
          <article className="v4-bento-main">
            <span className="v4-overline"><i /> Search V2</span>
            <h2>Escribe “Luffy”.<br />Nosotros resolvemos el resto.</h2>
            <p>La búsqueda normal agrupa la carta. Los filtros avanzados aparecen sólo cuando necesitas una versión concreta.</p>
            <div className="v4-demo-query">
              <span>⌕</span><strong>Luffy</strong><kbd>ENTER</kbd>
            </div>
            <div className="v4-demo-results">
              <span><b>Monkey D. Luffy</b><small>Personaje · 31 versiones</small></span>
              <span><b>Monkey D. Luffy</b><small>Leader · 18 versiones</small></span>
              <span><b>Monkey D. Luffy</b><small>Promo · 9 versiones</small></span>
            </div>
          </article>

          <article className="v4-bento-card v4-bento-purple">
            <span>01</span>
            <h3>Edición exacta</h3>
            <p>Set, idioma, rareza y acabado separados.</p>
            <div className="v4-mini-tags"><i>OP05</i><i>Alt art</i><i>EN</i></div>
          </article>

          <article className="v4-bento-card">
            <span>02</span>
            <h3>Portfolio honesto</h3>
            <p>Sin estimaciones inventadas. Precio, fuente y fecha.</p>
            <strong className="v4-price">€1.284,40</strong>
          </article>

          <article className="v4-bento-card">
            <span>03</span>
            <h3>Wishlist limpia</h3>
            <p>Guarda la versión que buscas, no sólo el nombre.</p>
            <div className="v4-heart-line"><b>♡</b><span>24 cartas</span></div>
          </article>
        </div>
      </section>

      <section id="news" className="v4-section app-shell">
        <div className="v4-release-panel">
          <div>
            <span className="v4-overline"><i /> Calendario oficial</span>
            <h2>Lo próximo, con región y fuente.</h2>
            <p>Sin convertir rumores ni fechas de otro mercado en datos.</p>
          </div>
          <div className="v4-release-list">
            <Link href="/games/pokemon#lanzamientos"><span>US</span><strong>Pokémon</strong><small>Ver calendario →</small></Link>
            <Link href="/games/magic#lanzamientos"><span>GLOBAL</span><strong>Magic</strong><small>Ver calendario →</small></Link>
            <Link href="/games/yugioh#lanzamientos"><span>EU</span><strong>Yu‑Gi‑Oh!</strong><small>Ver calendario →</small></Link>
          </div>
        </div>
      </section>

      <section className="v4-final app-shell">
        <div>
          <span className="v4-overline"><i /> Don’tRipIt</span>
          <h2>Tu colección merece algo mejor que una hoja de cálculo.</h2>
        </div>
        <Link href="/register" className="v4-button v4-button-light">Empezar gratis <span>→</span></Link>
      </section>

      <SiteFooter />
    </main>
  )
}
