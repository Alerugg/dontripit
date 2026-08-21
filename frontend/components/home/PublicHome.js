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

const IDENTITY_LEDGER = [
  {
    step: '01',
    label: 'Carta',
    tag: 'CANÓNICA',
    title: 'La identidad que conoces',
    copy: 'Nombre, número, set y rareza. Aquí todavía no existe un precio universal.',
  },
  {
    step: '02',
    label: 'Impresión',
    tag: 'OBJETO FÍSICO',
    title: 'La versión que realmente tienes',
    copy: 'Idioma, acabado, variante y región convierten la carta en una impresión física exacta.',
    accent: true,
  },
  {
    step: '03',
    label: 'Mercado',
    tag: 'FUENTE SEGURA',
    title: 'El precio de esa impresión',
    copy: 'Mostramos mercado únicamente cuando existe una correspondencia exacta y verificable.',
  },
]

export default function PublicHome() {
  const activeGames = GAME_CATALOG.filter((game) => game.slug !== 'riftbound')

  return (
    <main className="canva-home v5-home">
      <TopNav />

      <section className="v15-hero" aria-labelledby="home-title">
        <div className="v15-hero-grid-etch" aria-hidden="true" />
        <div className="v15-hero-glow" aria-hidden="true" />

        <div className="app-shell v15-hero-grid">
          <div className="v15-hero-copy">
            <span className="v15-hero-kicker"><i /> Infraestructura de datos para TCG</span>
            <h1 id="home-title">
              Encuentra la carta.<br />
              <em>Elige la exacta.</em>
            </h1>
            <p className="v15-hero-lead">
              Cada carta puede tener decenas de ediciones. Don’tRipIt te lleva desde la carta que conoces hasta la impresión física exacta —set, idioma, acabado y variante— y muestra mercado solo cuando la correspondencia es segura.
            </p>

            <div className="v15-hero-search" id="search">
              <HomeSearch />
            </div>

            <div className="v15-hero-actions" aria-label="Acciones principales">
              <Link href="/explorer" className="v15-hero-action v15-hero-action-primary">Explorar catálogo →</Link>
              <a href="#how-it-works" className="v15-hero-action">Cómo funciona</a>
            </div>
          </div>

          <aside className="v15-identity-ledger" aria-label="Cadena Carta, impresión y mercado">
            <div className="v15-ledger-head">
              <span>Cadena de identidad</span>
              <small>CARD → PRINT → MARKET</small>
            </div>

            <ol className="v15-ledger-list">
              {IDENTITY_LEDGER.map((item) => (
                <li key={item.step} className={item.accent ? 'is-accent' : ''}>
                  <span className="v15-ledger-step">{item.step}</span>
                  <div className="v15-ledger-body">
                    <div className="v15-ledger-labels">
                      <strong>{item.label}</strong>
                      <small>{item.tag}</small>
                    </div>
                    <h2>{item.title}</h2>
                    <p>{item.copy}</p>
                  </div>
                </li>
              ))}
            </ol>

            <div className="v15-ledger-note">
              <i aria-hidden="true" />
              <span>Sin mapeo exacto no mostramos precio. Nunca estimamos valores de otra edición.</span>
            </div>
          </aside>
        </div>
      </section>

      <section className="canva-signal-strip" aria-label="Qué ofrece Don’tRipIt">
        <div className="app-shell canva-signal-grid">
          <div><strong>{activeGames.length}</strong><span>juegos activos</span></div>
          <div><strong>Exacta</strong><span>identidad física</span></div>
          <div><strong>Cardmarket</strong><span>referencia de mercado</span></div>
          <div><strong>Regional</strong><span>fuente y procedencia visibles</span></div>
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
          <Link href="/explorer" className="canva-secondary-link">Buscar en todos →</Link>
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
          <p>Las fuentes se mantienen separadas por región y cada elemento publicado conserva fecha y procedencia verificables.</p>
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
            <span>INTEGRIDAD</span>
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
          <Link href="/explorer" className="dri-btn dri-btn-ghost">Explorar catálogo</Link>
        </div>
      </section>

      <SiteFooter />
    </main>
  )
}
