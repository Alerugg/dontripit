import Link from 'next/link'
import TopNav from '../layout/TopNav'
import SiteFooter from '../layout/SiteFooter'
import HomeSearch from './HomeSearch'
import HomeRevealV3 from './HomeRevealV3'
import { GAME_CATALOG } from '../../lib/catalog/games'

const PRINT_OBJECTS = [
  ['Original', 'Set · idioma · normal'],
  ['Holo', 'Mismo nombre · otro acabado'],
  ['Reverse', 'Otra variante física'],
  ['Paralela', 'Otra región · otro idioma'],
]

const PORTFOLIO_ROWS = [
  ['Mapeado', 'safe'],
  ['En revisión', 'review'],
  ['Sin precio seguro', 'empty'],
]

const PROVENANCE = [
  ['EU', 'Ediciones e idiomas europeos'],
  ['US', 'Impresiones norteamericanas'],
  ['JP', 'Origen japonés y variantes'],
]

function shortGameName(game) {
  if (game.slug === 'magic') return 'Magic'
  if (game.slug === 'onepiece') return 'One Piece'
  return game.name
}

export default function PublicHome() {
  const activeGames = GAME_CATALOG.filter((game) => game.availability === 'active')

  return (
    <main className="v17-home">
      <TopNav />

      <section className="v17-cover" aria-labelledby="home-title">
        <div className="v17-cover-grid" aria-hidden="true" />
        <div className="v17-cover-glow" aria-hidden="true" />

        <div className="v17-cover-rail">
          <div className="app-shell">
            <strong>Don’tRipIt</strong>
            <span>Catálogo multi-TCG</span>
            <small>Card → Print → Market</small>
          </div>
        </div>

        <div className="app-shell v17-cover-body">
          <div className="v17-cover-copy">
            <span className="v17-kicker">Portada · catálogo físico</span>
            <h1 id="home-title">
              La misma carta.<br />
              <em>Objetos</em> distintos.
            </h1>
            <p>Busca por nombre. Aterriza en la impresión física exacta.</p>

            <div className="v17-cover-search" id="search">
              <HomeSearch />
            </div>
          </div>

          <div className="v17-print-scene" aria-hidden="true">
            {[0, 1, 2].map((index) => (
              <div key={index} className={`v17-floating-print is-${index + 1}`}>
                <div className="v17-floating-art" />
                <span>PRINT {String(index + 1).padStart(2, '0')}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="v17-cover-games" aria-label="Juegos disponibles">
          <div className="app-shell">
            {activeGames.map((game) => (
              <Link key={game.slug} href={`/games/${game.slug}`} style={{ '--game-accent': game.accent }}>
                <i aria-hidden="true" />{shortGameName(game)}
              </Link>
            ))}
          </div>
        </div>
      </section>

      <section className="v17-marquee" aria-label="Modelo de identidad Don’tRipIt">
        <div className="v17-marquee-track" aria-hidden="true">
          {[0, 1].map((copy) => (
            <span key={copy}>
              Carta <i /> Impresión <i /> Set <i /> Idioma <i /> Acabado <i /> Variante <i /> Región <i /> Correspondencia segura <i />
            </span>
          ))}
        </div>
      </section>

      <HomeRevealV3 as="section" className="v17-gallery-section">
        <div className="app-shell">
          <header className="v17-number-head">
            <b>01</b>
            <div>
              <h2>Objetos físicos</h2>
              <span>Identidad ≠ edición</span>
            </div>
          </header>

          <div className="v17-object-gallery">
            {PRINT_OBJECTS.map(([label, meta], index) => (
              <figure key={label} className={index % 2 ? 'is-offset' : ''}>
                <div className={`v17-object-card is-tone-${index}`} aria-hidden="true">
                  <span>0{index + 1}</span>
                  <i />
                </div>
                <figcaption>
                  <strong>{label}</strong>
                  <small>{meta}</small>
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v17-truth-section">
        <div className="v17-truth-grid" aria-hidden="true" />
        <div className="v17-truth-glow" aria-hidden="true" />
        <div className="app-shell v17-truth-inner">
          <h2>Si no hay correspondencia segura,<br /><em>no hay precio.</em></h2>
          <p>El precio pertenece a una Print exacta. Cardmarket solo aparece cuando el mapeo es verificable.</p>
          <small>Sin mapeo exacto no mostramos precio.</small>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v17-games-section" id="games">
        <div className="app-shell">
          <header className="v17-number-head v17-number-head-dark">
            <b>02</b>
            <div>
              <h2>Salas</h2>
              <span>Cuatro catálogos activos</span>
            </div>
          </header>

          <div className="v17-game-gallery">
            {activeGames.map((game, index) => (
              <Link
                key={game.slug}
                href={`/games/${game.slug}`}
                className={`v17-game-panel is-${index + 1}`}
                style={{ '--game-accent': game.accent }}
              >
                <span className="v17-game-code">{game.slug === 'onepiece' ? 'OP' : game.slug === 'yugioh' ? 'YGO' : game.slug === 'pokemon' ? 'PKM' : 'MTG'}</span>
                <i className="v17-game-bg-code" aria-hidden="true">{game.slug === 'onepiece' ? 'OP' : game.slug === 'yugioh' ? 'YGO' : game.slug === 'pokemon' ? 'PKM' : 'MTG'}</i>
                <div>
                  <strong>{shortGameName(game)}</strong>
                  <small>Entrar →</small>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </HomeRevealV3>

      <HomeRevealV3 as="section" className="v17-portfolio-section">
        <div className="app-shell v17-portfolio-grid">
          <div className="v17-portfolio-copy">
            <b>03</b>
            <h2>Tu colección,<br />impresión a impresión.</h2>
            <p>Solo se valora lo que tiene correspondencia segura.</p>
            <div>
              <Link href="/collection">Colección</Link>
              <Link href="/wishlist">Wishlist</Link>
            </div>
          </div>

          <div className="v17-portfolio-sheet" aria-label="Ejemplo conceptual de identidad y valoración">
            <header><span>Impresiones seguidas</span><small>Estado de precio</small></header>
            <ul>
              {PORTFOLIO_ROWS.map(([state, kind], index) => (
                <li key={state}>
                  <div>
                    <strong>Carta canónica · impresión exacta</strong>
                    <small>Set · #0{index + 1} · idioma · acabado · variante</small>
                  </div>
                  <span className={`is-${kind}`}><i aria-hidden="true" />{state}</span>
                  <b className={kind === 'safe' ? 'has-value' : ''} aria-label={kind === 'safe' ? 'Precio verificable disponible' : 'Valor retenido'} />
                </li>
              ))}
            </ul>
            <footer><span>Valor conservador</span><strong>Solo Prints con precio seguro</strong></footer>
          </div>
        </div>
      </HomeRevealV3>

      <section className="v17-provenance" aria-label="Procedencia regional">
        <div className="app-shell">
          {PROVENANCE.map(([region, text]) => (
            <div key={region}><strong>{region}</strong><span>{text}</span></div>
          ))}
        </div>
        <p>Fuente y procedencia visibles · Sin fechas inventadas</p>
      </section>

      <HomeRevealV3 as="section" className="v17-closing">
        <div className="app-shell v17-closing-grid">
          <h2>Colecciona<br />con precisión.</h2>
          <div>
            <p>Empieza por un nombre. Termina en la impresión correcta.</p>
            <nav aria-label="Acciones finales">
              <a href="#search">Buscar una carta ↑</a>
              <Link href="/explorer">Explorar catálogo</Link>
            </nav>
          </div>
        </div>
      </HomeRevealV3>

      <SiteFooter />
    </main>
  )
}
