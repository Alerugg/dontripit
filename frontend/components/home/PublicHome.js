import Link from 'next/link'
import TopNav from '../layout/TopNav'
import SiteFooter from '../layout/SiteFooter'
import { GAME_CATALOG } from '../../lib/catalog/games'

const gamePresentation = {
  pokemon: {
    logo: '/games/pokemon/pokemon_logo.png',
    note: 'Sets, rarezas y variantes físicas sin perderte entre duplicados.',
  },
  magic: {
    logo: '/games/magic/magic_logo.png',
    note: 'Encuentra una carta y abre todas sus impresiones cuando realmente las necesites.',
  },
  onepiece: {
    logo: '/games/onepiece/onepiece_logo.png',
    note: 'Leaders, alternates, promos y reprints organizados de forma visual.',
  },
  yugioh: {
    logo: '/games/yugioh/yugioh_logo.png',
    note: 'Busca por nombre y llega a la edición exacta sin navegar una tabla infinita.',
  },
  riftbound: {
    logo: '/games/riftbound/riftbound_logo.png',
    note: 'Próximamente. Abriremos el catálogo cuando el acceso de producción esté listo.',
  },
}

const featureCards = [
  {
    icon: '⌕',
    title: 'Busca como lo dirías',
    copy: 'Escribe “Pikachu 151”, “Luffy OP05” o “Blue-Eyes”. Don’tRipIt se ocupa de la parte técnica.',
  },
  {
    icon: '◇',
    title: 'Tu colección, de verdad',
    copy: 'Guarda la versión exacta que tienes, controla cantidades y construye tu portfolio sin hojas de cálculo.',
  },
  {
    icon: '♡',
    title: 'Wishlist sin ruido',
    copy: 'Marca lo que buscas y vuelve a ello en segundos. Nada de rehacer filtros cada vez que entras.',
  },
  {
    icon: '↗',
    title: 'Sigue lo que viene',
    copy: 'Noticias y próximos lanzamientos por juego y región, reunidos en un único lugar.',
  },
]

export default function PublicHome() {
  return (
    <main className="dri-site">
      <TopNav />

      <section className="dri-hero app-shell">
        <div className="dri-hero-copy">
          <div className="dri-pill dri-pill-soft">Tu app de colección TCG</div>
          <h1>
            Tu colección,
            <span> sin caos.</span>
          </h1>
          <p className="dri-hero-lead">
            Encuentra la carta exacta, guarda lo que tienes, crea tu wishlist y sigue los próximos
            lanzamientos de tus TCG favoritos desde una experiencia hecha para coleccionistas, no para hojas de cálculo.
          </p>

          <div className="dri-hero-actions">
            <Link href="/register" className="dri-btn dri-btn-primary dri-btn-lg">Empezar gratis</Link>
            <a href="#games" className="dri-btn dri-btn-ghost dri-btn-lg">Ver juegos</a>
          </div>

          <div className="dri-trust-line">
            <span>Gratis durante el MVP</span>
            <i />
            <span>4 TCG disponibles</span>
            <i />
            <span>Riftbound próximamente</span>
          </div>
        </div>

        <div className="dri-hero-demo" aria-label="Vista previa de Don’tRipIt">
          <div className="dri-demo-window">
            <div className="dri-demo-topbar">
              <span className="dri-demo-dot" />
              <span className="dri-demo-dot" />
              <span className="dri-demo-dot" />
              <small>Don’tRipIt</small>
            </div>
            <div className="dri-demo-search">
              <span aria-hidden="true">⌕</span>
              <strong>Pikachu 151</strong>
              <kbd>↵</kbd>
            </div>
            <div className="dri-demo-result-grid">
              <article className="dri-demo-card dri-demo-card-main">
                <div className="dri-demo-card-art">151</div>
                <div>
                  <small>Pokémon</small>
                  <strong>Pikachu</strong>
                  <span>12 versiones</span>
                </div>
              </article>
              <article className="dri-demo-card">
                <div className="dri-demo-card-art dri-demo-card-art-two">OP</div>
                <div>
                  <small>One Piece</small>
                  <strong>Monkey D. Luffy</strong>
                  <span>Ver versiones</span>
                </div>
              </article>
              <article className="dri-demo-card dri-demo-card-compact">
                <span className="dri-demo-heart">♡</span>
                <small>Wishlist</small>
                <strong>24 cartas</strong>
              </article>
            </div>
          </div>
          <div className="dri-hero-orb dri-hero-orb-one" />
          <div className="dri-hero-orb dri-hero-orb-two" />
        </div>
      </section>

      <section id="features" className="dri-section app-shell">
        <div className="dri-section-head">
          <div>
            <span className="dri-kicker">Menos fricción. Más colección.</span>
            <h2>Todo lo importante, sin obligarte a aprender la base de datos.</h2>
          </div>
          <p>
            La complejidad sigue existiendo, pero la escondemos hasta el momento exacto en que te hace falta.
          </p>
        </div>

        <div className="dri-feature-grid">
          {featureCards.map((feature) => (
            <article key={feature.title} className="dri-feature-card">
              <span className="dri-feature-icon" aria-hidden="true">{feature.icon}</span>
              <h3>{feature.title}</h3>
              <p>{feature.copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="games" className="dri-section dri-section-games">
        <div className="app-shell">
          <div className="dri-section-head dri-section-head-light">
            <div>
              <span className="dri-kicker">Un hogar para cada TCG</span>
              <h2>Cada juego se siente suyo. La experiencia sigue siendo la misma.</h2>
            </div>
            <p>Sin mezclar reglas, rarezas ni filtros entre universos distintos.</p>
          </div>

          <div className="dri-game-grid">
            {GAME_CATALOG.map((game) => {
              const presentation = gamePresentation[game.slug]
              const isComingSoon = game.slug === 'riftbound'
              return (
                <Link
                  key={game.slug}
                  href={`/register?next=${encodeURIComponent(`/games/${game.slug}`)}`}
                  className={`dri-game-card dri-game-${game.slug}`}
                >
                  <div className="dri-game-card-glow" />
                  <div className="dri-game-logo-wrap">
                    {presentation?.logo ? <img src={presentation.logo} alt={game.name} className="dri-game-logo" /> : <strong>{game.name}</strong>}
                  </div>
                  <p>{presentation?.note || game.description}</p>
                  <span className="dri-game-card-link">{isComingSoon ? 'Próximamente' : 'Explorar'} <b aria-hidden="true">→</b></span>
                </Link>
              )
            })}
          </div>
        </div>
      </section>

      <section className="dri-section app-shell">
        <div className="dri-steps-panel">
          <div className="dri-section-head dri-section-head-compact">
            <div>
              <span className="dri-kicker">Así de simple</span>
              <h2>De “¿qué carta es?” a “ya está en mi colección”.</h2>
            </div>
          </div>
          <ol className="dri-steps">
            <li>
              <span>01</span>
              <strong>Busca</strong>
              <p>Nombre, número, set o lo que recuerdes.</p>
            </li>
            <li>
              <span>02</span>
              <strong>Elige la versión</strong>
              <p>Solo abrimos rareza, idioma y variante cuando lo necesitas.</p>
            </li>
            <li>
              <span>03</span>
              <strong>Guárdala</strong>
              <p>A tu colección o a tu wishlist con una sola acción.</p>
            </li>
          </ol>
        </div>
      </section>

      <section id="news" className="dri-section app-shell">
        <div className="dri-news-preview">
          <div className="dri-news-preview-copy">
            <span className="dri-kicker">Noticias + próximos lanzamientos</span>
            <h2>No vuelvas a enterarte tarde de una promo o un set.</h2>
            <p>
              Cada hub tendrá un feed limpio con fuentes oficiales, próximos lanzamientos y región.
              Japón, USA y Europa donde tenga sentido para cada juego.
            </p>
            <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta gratis</Link>
          </div>

          <div className="dri-news-cards" aria-hidden="true">
            <article>
              <span>JP</span>
              <small>Próximamente</small>
              <strong>Nuevo set</strong>
              <p>Fecha · Producto · Fuente oficial</p>
            </article>
            <article>
              <span>US</span>
              <small>Noticias</small>
              <strong>Promos y anuncios</strong>
              <p>Solo lo relevante para coleccionar</p>
            </article>
            <article>
              <span>EU</span>
              <small>Lanzamientos</small>
              <strong>Calendario europeo</strong>
              <p>Una vista, sin saltar entre diez webs</p>
            </article>
          </div>
        </div>
      </section>

      <section className="dri-final-cta">
        <div className="app-shell dri-final-cta-inner">
          <div>
            <span className="dri-kicker">Don’tRipIt</span>
            <h2>Menos buscar. Más disfrutar tu colección.</h2>
          </div>
          <Link href="/register" className="dri-btn dri-btn-inverse dri-btn-lg">Crear mi cuenta</Link>
        </div>
      </section>

      <SiteFooter />
    </main>
  )
}
