import Image from 'next/image'
import Link from 'next/link'
import TopNav from '../layout/TopNav'
import SiteFooter from '../layout/SiteFooter'
import HomeSearch from './HomeSearch'
import HomeRevealV3 from './HomeRevealV3'
import HomeIdentityStoryV3 from './HomeIdentityStoryV3'
import { GAME_CATALOG } from '../../lib/catalog/games'

const GAME_LOGOS = {
  pokemon: '/games/pokemon/pokemon_logo.png',
  magic: '/games/magic/magic_logo.png',
  onepiece: '/games/onepiece/onepiece_logo.png',
  yugioh: '/games/yugioh/yugioh_logo.png',
}

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

const PRINCIPLES = [
  {
    kind: 'identity',
    eyebrow: 'IDENTIDAD',
    title: 'La impresión exacta',
    copy: 'Set, idioma, acabado y variante. La unidad real que compras y guardas.',
  },
  {
    kind: 'market',
    eyebrow: 'INTEGRIDAD',
    title: 'Mercado con fuente',
    copy: 'Solo mostramos mercado cuando la correspondencia es segura.',
  },
  {
    kind: 'multi',
    eyebrow: 'MULTI-TCG',
    title: 'Una lógica común',
    copy: 'Cada juego conserva su identidad sin romper el modelo Card → Print → Market.',
  },
]

const PRINT_EXAMPLES = [
  ['EN', 'Holo', 'Estándar'],
  ['ES', 'Reverse Holo', 'Estándar'],
  ['JP', 'Normal', 'Paralela'],
  ['DE', 'Foil', 'Promo'],
]

const PORTFOLIO_ROWS = [
  ['EN', 'Holo', 'Precio seguro', 'safe'],
  ['ES', 'Reverse Holo', 'En revisión', 'review'],
  ['JP', 'Paralela', 'Sin precio seguro', 'empty'],
]

const REGIONS = [
  ['EU', 'Europa', 'Noticias y lanzamientos oficiales de la región'],
  ['US', 'Estados Unidos', 'Fuentes oficiales regionales separadas'],
  ['JP', 'Japón', 'Procedencia japonesa visible y verificable'],
]

export default function PublicHome() {
  const activeGames = GAME_CATALOG.filter((game) => game.slug !== 'riftbound')

  return (
    <main className="canva-home v5-home v16-home">
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

      <section className="v16-principles" aria-label="Principios de Don’tRipIt">
        <div className="v16-principles-line" aria-hidden="true" />
        <div className="app-shell v16-principles-grid">
          {PRINCIPLES.map((item, index) => (
            <HomeRevealV3 key={item.eyebrow} delay={index * 80} className="v16-principle">
              <div className={`v16-principle-glyph is-${item.kind}`} aria-hidden="true">
                <i /><b /><span />
              </div>
              <div>
                <small>{item.eyebrow}</small>
                <strong>{item.title}</strong>
                <p>{item.copy}</p>
              </div>
            </HomeRevealV3>
          ))}
        </div>
      </section>

      <HomeRevealV3 as="section" className="v16-section v16-story-section" id="how-it-works">
        <div className="app-shell">
          <div className="v16-section-heading">
            <span>Cómo funciona</span>
            <h2>Tres capas. Una sola verdad.</h2>
            <p>Recorre la cadena y mira qué cambia en cada paso.</p>
          </div>
          <HomeIdentityStoryV3 />
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v16-section v16-edition-section">
        <div className="app-shell v16-split">
          <div className="v16-edition-copy">
            <span>Identidad vs objeto</span>
            <h2>Una carta no es<br />una <em>edición</em>.</h2>
            <p>El nombre identifica. La impresión define. Dos objetos con el mismo nombre pueden ser versiones físicas completamente distintas.</p>
            <Link href="/explorer" className="v16-text-link">Ver la diferencia en el explorador →</Link>
          </div>

          <div className="v16-edition-demo" aria-label="Ejemplo conceptual de carta e impresiones">
            <div className="v16-canonical-demo">
              <span>CARD</span>
              <div className="v16-demo-card" aria-hidden="true"><i /><b /><small /></div>
              <p>Una identidad.<br />Sin precio propio.</p>
            </div>
            <div className="v16-print-demo">
              <header><span>PRINTS</span><small>OBJETOS DISTINTOS</small></header>
              <ul>
                {PRINT_EXAMPLES.map(([lang, finish, variant]) => (
                  <li key={lang}><b>{lang}</b><strong>{finish}</strong><small>{variant}</small></li>
                ))}
              </ul>
              <p>Cada fila representa una identidad física diferente.</p>
            </div>
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v16-section v16-games-section" id="games">
        <div className="app-shell">
          <div className="v16-section-heading v16-heading-row">
            <div>
              <span>Catálogos</span>
              <h2>Cada TCG, su propio terreno.</h2>
            </div>
            <Link href="/explorer" className="v16-text-link">Buscar en todos →</Link>
          </div>

          <div className="v16-game-list">
            {activeGames.map((game, index) => (
              <HomeRevealV3 key={game.slug} delay={index * 70}>
                <Link href={`/games/${game.slug}`} className="v16-game-row" style={{ '--game-accent': game.accent }}>
                  <div className="v16-game-mark">
                    <Image src={GAME_LOGOS[game.slug]} alt="" width={260} height={96} sizes="(max-width: 700px) 130px, 190px" />
                  </div>
                  <div className="v16-game-row-copy">
                    <small>CATÁLOGO ACTIVO</small>
                    <strong>{game.name}</strong>
                    <p>{game.description}</p>
                  </div>
                  <b aria-hidden="true">↗</b>
                </Link>
              </HomeRevealV3>
            ))}
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v16-section v16-portfolio-section">
        <div className="app-shell v16-split v16-portfolio-split">
          <div className="v16-portfolio-copy">
            <span>Colección y wishlist</span>
            <h2>Guarda la impresión.<br />No la suposición.</h2>
            <p>Cada entrada conserva su identidad exacta. Si una impresión no tiene un precio seguro, sigue visible pero no se inventa una valoración.</p>
            <div className="v16-inline-links">
              <Link href="/collection">Colección →</Link>
              <Link href="/wishlist">Wishlist →</Link>
              <Link href="/dashboard">Dashboard →</Link>
            </div>
          </div>

          <div className="v16-portfolio-demo" aria-label="Esquema conceptual de portfolio">
            <header><span>PORTFOLIO</span><small>ESQUEMA CONCEPTUAL</small></header>
            <ul>
              {PORTFOLIO_ROWS.map(([lang, finish, state, kind]) => (
                <li key={lang}>
                  <b>{lang}</b>
                  <div><strong>{finish}</strong><small>Impresión física exacta</small></div>
                  <span className={`is-${kind}`}>{state}</span>
                  <i className={kind === 'safe' ? 'has-value' : ''} aria-label={kind === 'safe' ? 'Valor disponible con fuente' : 'Sin valor publicado'} />
                </li>
              ))}
            </ul>
            <footer><span>Valor conservador</span><strong>Solo impresiones con precio seguro</strong></footer>
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v16-trust-band">
        <div className="v16-trust-pattern" aria-hidden="true" />
        <div className="app-shell v16-trust-inner">
          <h2>Sin precio inventado.<span>Ni uno.</span></h2>
          <div className="v16-trust-rules">
            <div><small>NUNCA</small><p>rellenamos un hueco con una estimación.</p></div>
            <div><small>SIEMPRE</small><p>mostramos procedencia cuando hay un dato.</p></div>
            <div><small>EXPLÍCITO</small><p>si falta certeza, decimos que falta.</p></div>
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v16-section v16-radar-section" id="releases">
        <div className="app-shell">
          <div className="v16-section-heading">
            <span>Radar regional</span>
            <h2>Lo oficial, por región.</h2>
            <p>Noticias y lanzamientos conservan su procedencia en vez de mezclarse en un único feed sin contexto.</p>
          </div>

          <div className="v16-radar-grid">
            {REGIONS.map(([code, title, copy], index) => (
              <HomeRevealV3 key={code} delay={index * 80} className="v16-radar-card">
                <header><strong>{code}</strong><small>{title}</small></header>
                <div className="v16-radar-signal" aria-hidden="true"><i /><b /><span /></div>
                <p>{copy}</p>
                <small>Fuente y procedencia visibles</small>
              </HomeRevealV3>
            ))}
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v16-final">
        <div className="v16-final-grid" aria-hidden="true" />
        <div className="v16-final-glow" aria-hidden="true" />
        <div className="app-shell v16-final-inner">
          <span><i /> Don’tRipIt</span>
          <h2>Empieza por el nombre.<br />Termina en la <em>versión exacta</em>.</h2>
          <p>Busca una carta y deja que el catálogo haga el trabajo difícil de separar sus impresiones.</p>
          <div className="v16-final-actions">
            <a href="#search" className="dri-btn dri-btn-primary">Buscar una carta ↑</a>
            <Link href="/explorer" className="dri-btn dri-btn-ghost">Explorar catálogo</Link>
          </div>
        </div>
      </HomeRevealV3>

      <SiteFooter />
    </main>
  )
}
