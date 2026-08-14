import Image from 'next/image'
import Link from 'next/link'
import TopNav from '../layout/TopNav'
import SiteFooter from '../layout/SiteFooter'
import HomeSearch from './HomeSearch'
import InstallAppPrompt from '../pwa/InstallAppPrompt'
import { GAME_CATALOG } from '../../lib/catalog/games'

const GAME_LOGOS = {
  pokemon: '/games/pokemon/pokemon_logo.png',
  magic: '/games/magic/magic_logo.png',
  onepiece: '/games/onepiece/onepiece_logo.png',
  yugioh: '/games/yugioh/yugioh_logo.png',
}

const WORKFLOW = [
  { step: '01', label: 'Buscar', title: 'Encuentra la carta', copy: 'Empieza por nombre, número o set. El catálogo te lleva hasta la identidad correcta.' },
  { step: '02', label: 'Seleccionar', title: 'Elige la versión física', copy: 'Set, idioma, acabado, rareza y variante permanecen separados para no mezclar impresiones.' },
  { step: '03', label: 'Guardar', title: 'Colección o wishlist', copy: 'Añade exactamente la edición que tienes o la que estás buscando desde tu cuenta.' },
]

const TRUST_POINTS = [
  ['Identidad', 'Versión física exacta', 'Set · idioma · acabado · variante'],
  ['Mercado', 'Precio verificable', 'Cardmarket con fuente y fecha cuando existe enlace exacto'],
  ['Portfolio', 'Sin inventar valor', 'Las versiones sin precio seguro no se estiman'],
]

const REGIONS = [
  ['EU', 'Europa', 'Fuentes oficiales regionales'],
  ['US', 'Estados Unidos', 'Fuentes oficiales regionales'],
  ['JP', 'Japón', 'Fuentes oficiales regionales'],
]

export default function PublicHome() {
  const activeGames = GAME_CATALOG.filter((game) => game.slug !== 'riftbound')
  const heroGames = activeGames.slice(0, 3)

  return (
    <main className="canva-home">
      <TopNav />

      <section className="canva-hero app-shell" aria-labelledby="home-title">
        <div className="canva-ambient" aria-hidden="true">
          <span className="canva-orb canva-orb-one" />
          <span className="canva-orb canva-orb-two" />
        </div>

        <div className="canva-hero-copy">
          <span className="canva-eyebrow"><i /> Catálogo TCG</span>
          <h1 id="home-title">Tu colección empieza por encontrar <em>la versión correcta.</em></h1>
          <p className="canva-hero-sub">Busca una carta, identifica la edición física exacta y guárdala con una referencia de mercado trazable cuando existe correspondencia segura con Cardmarket.</p>

          <div className="canva-hero-search" id="search">
            <HomeSearch />
          </div>

          <div className="canva-hero-actions">
            <Link href="/register" className="dri-btn dri-btn-primary canva-primary-cta">Crear mi colección</Link>
            <Link href="/search" className="canva-secondary-link">Búsqueda avanzada →</Link>
            <InstallAppPrompt compact />
          </div>

          <ul className="canva-proof" aria-label="Características del catálogo">
            <li><b>✓</b> {activeGames.length} TCG activos</li>
            <li><b>✓</b> Prints separados por edición</li>
            <li><b>✓</b> Actualización diaria de datos</li>
          </ul>
        </div>

        <div className="canva-visual" aria-label="Vista previa de los catálogos activos">
          <div className="canva-stack-glow" aria-hidden="true" />
          {heroGames.map((game, index) => (
            <Link
              key={game.slug}
              href={`/games/${game.slug}`}
              className={`canva-preview-card canva-preview-card-${index + 1}`}
              style={{ '--game-accent': game.accent }}
            >
              <span className="canva-preview-badge">Catálogo activo</span>
              <div className="canva-preview-art">
                <Image src={GAME_LOGOS[game.slug]} alt={game.name} width={240} height={100} sizes="(max-width: 760px) 120px, 180px" priority={index === 0} />
              </div>
              <div className="canva-preview-copy">
                <strong>{game.name}</strong>
                <small>Cartas · sets · versiones</small>
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section className="canva-signal-strip" aria-label="Qué ofrece Don’tRipIt">
        <div className="app-shell canva-signal-grid">
          <div><strong>{activeGames.length}</strong><span>juegos activos</span></div>
          <div><strong>Exacta</strong><span>identidad física</span></div>
          <div><strong>Cardmarket</strong><span>referencia de mercado</span></div>
          <div><strong>24h</strong><span>proceso de actualización</span></div>
        </div>
      </section>

      <section className="canva-section app-shell" id="how-it-works">
        <div className="canva-section-head">
          <div>
            <span className="canva-eyebrow">Proceso</span>
            <h2>Tres pasos. Sin perderte entre versiones.</h2>
          </div>
          <p>La navegación sigue el orden natural de un coleccionista: carta primero, impresión exacta después.</p>
        </div>
        <div className="canva-steps">
          {WORKFLOW.map((item) => (
            <article key={item.step} className="canva-step">
              <span>{item.step} · {item.label}</span>
              <h3>{item.title}</h3>
              <p>{item.copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="canva-section app-shell" id="games">
        <div className="canva-section-head canva-section-head-inline">
          <div>
            <span className="canva-eyebrow">Catálogos</span>
            <h2>Elige tu TCG y entra directo al catálogo.</h2>
          </div>
          <Link href="/search" className="canva-secondary-link">Buscar en todos →</Link>
        </div>

        <div className="canva-game-grid">
          {activeGames.map((game) => (
            <Link key={game.slug} href={`/games/${game.slug}`} className="canva-game-card" style={{ '--game-accent': game.accent }}>
              <span className="canva-game-state">Catálogo activo</span>
              <div className="canva-game-logo">
                <Image src={GAME_LOGOS[game.slug]} alt={game.name} width={250} height={90} sizes="(max-width: 620px) 150px, 190px" />
              </div>
              <div className="canva-game-copy">
                <strong>{game.name}</strong>
                <p>{game.description}</p>
                <span>Explorar →</span>
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section className="canva-section app-shell">
        <div className="canva-section-head">
          <div>
            <span className="canva-eyebrow">Tu portfolio</span>
            <h2>Todo lo que guardas, por edición exacta.</h2>
          </div>
          <p>La cuenta usa la misma identidad física que el catálogo. Colección, wishlist y valoración no mezclan versiones distintas.</p>
        </div>

        <div className="canva-portfolio">
          <div className="canva-portfolio-main">
            <div className="canva-portfolio-tabs" aria-label="Herramientas de cuenta">
              <span className="is-active">Colección</span>
              <span>Wishlist</span>
              <span>Progreso</span>
            </div>
            <span className="canva-eyebrow">Una sola cuenta</span>
            <h3>Construye un inventario que represente lo que realmente tienes.</h3>
            <p>Guarda cantidades y versiones exactas. Cuando una impresión tiene precio seguro, Don’tRipIt puede incorporarlo sin extrapolar otras variantes.</p>
            <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta gratis</Link>
          </div>

          <div className="canva-trust-list">
            {TRUST_POINTS.map(([label, title, copy]) => (
              <div key={label} className="canva-trust-row">
                <span>{label}</span>
                <div><strong>{title}</strong><small>{copy}</small></div>
                <b aria-hidden="true">✓</b>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="canva-section app-shell" id="releases">
        <div className="canva-section-head">
          <div>
            <span className="canva-eyebrow">Contenido regional</span>
            <h2>Noticias y lanzamientos con fuente oficial.</h2>
          </div>
          <p>El proceso regional se actualiza a diario y mantiene separadas las fuentes de Europa, Estados Unidos y Japón.</p>
        </div>
        <div className="canva-region-grid">
          {REGIONS.map(([code, title, copy]) => (
            <article key={code} className="canva-region-card">
              <span>{code}</span>
              <strong>{title}</strong>
              <small>{copy}</small>
            </article>
          ))}
          <article className="canva-region-card canva-region-card-accent">
            <span>DIARIO</span>
            <strong>Sin fechas inventadas</strong>
            <small>Solo se publica lo que llega de las fuentes activas verificadas.</small>
          </article>
        </div>
      </section>

      <section className="canva-final-cta app-shell">
        <div>
          <span className="canva-eyebrow"><i /> Don’tRipIt</span>
          <h2>Una carta. Una versión exacta. Una colección limpia.</h2>
        </div>
        <div className="canva-final-actions">
          <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta</Link>
          <Link href="/search" className="dri-btn dri-btn-ghost">Explorar catálogo</Link>
        </div>
      </section>

      <SiteFooter />
    </main>
  )
}
