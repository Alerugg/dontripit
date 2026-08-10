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
  pokemon: 'Cartas, sets, promos y variantes',
  magic: 'Impresiones, finishes e idiomas',
  onepiece: 'Leaders, promos, parallels y reprints',
  yugioh: 'Ediciones, rarezas y códigos',
  riftbound: 'El siguiente catálogo que estamos preparando',
}

export default function PublicHome() {
  const activeGames = GAME_CATALOG.filter((game) => game.slug !== 'riftbound')
  const riftbound = GAME_CATALOG.find((game) => game.slug === 'riftbound')

  return (
    <main className="dri-home">
      <TopNav />

      <section className="dri-home-hero app-shell">
        <div className="dri-home-hero-copy">
          <span className="v4-overline"><i /> Tu colección, bien identificada</span>
          <h1>Encuentra la carta.<br /><em>Guarda la versión exacta.</em></h1>
          <p>
            Busca por nombre, número o set. Don’tRipIt separa cada edición física, conecta su precio con la fuente y te deja organizar colección y wishlist sin mezclar variantes.
          </p>

          <div id="search" className="dri-home-search-zone">
            <HomeSearch />
          </div>

          <div className="dri-home-hero-actions">
            <Link href="/register" className="dri-btn dri-btn-primary">Crear cuenta gratis</Link>
            <a href="#games" className="dri-btn dri-btn-ghost">Explorar catálogos ↓</a>
          </div>

          <ul className="dri-home-proof" aria-label="Principios del producto">
            <li><b>✓</b>Identidad física exacta</li>
            <li><b>✓</b>Precio con fuente y fecha</li>
            <li><b>✓</b>Colección y wishlist por versión</li>
          </ul>
        </div>

        <div className="dri-home-product-preview" aria-label="Ejemplo del flujo de Don’tRipIt">
          <div className="dri-preview-window">
            <div className="dri-preview-top">
              <span>Ejemplo de interfaz</span>
              <strong>Don’tRipIt</strong>
            </div>
            <div className="dri-preview-search">
              <span>⌕</span>
              <strong>OP05-119</strong>
            </div>
            <div className="dri-preview-result">
              <div className="dri-preview-card-art">DRI</div>
              <div className="dri-preview-result-copy">
                <small>Resultado exacto</small>
                <strong>Elige la edición que tienes</strong>
                <div className="dri-preview-tags">
                  <i>Set</i><i>Rareza</i><i>Idioma</i><i>Variante</i>
                </div>
              </div>
            </div>
            <div className="dri-preview-action-row">
              <span>+ Mi colección</span>
              <span>♡ Wishlist</span>
            </div>
          </div>
        </div>
      </section>

      <section className="dri-capability-bar">
        <div className="app-shell">
          <span>4 catálogos activos</span><i />
          <span>Cardmarket como fuente de mercado</span><i />
          <span>Variantes y reprints separados</span><i />
          <span>Lanzamientos oficiales por región</span>
        </div>
      </section>

      <section id="games" className="dri-home-section app-shell">
        <header className="dri-home-heading">
          <div>
            <span className="v4-overline"><i /> Catálogos</span>
            <h2>Cuatro juegos. Una forma clara de coleccionar.</h2>
          </div>
          <p>La interfaz es consistente; los filtros y atributos respetan las reglas de cada TCG.</p>
        </header>

        <div className="dri-game-grid">
          {activeGames.map((game) => (
            <Link
              key={game.slug}
              href={`/games/${game.slug}`}
              className="dri-game-card"
              style={{ '--game-accent': game.accent }}
            >
              <span className="dri-game-card-state">Explorar catálogo</span>
              <div className="dri-game-card-logo">
                <Image src={GAME_LOGOS[game.slug]} alt={game.name} width={280} height={100} sizes="210px" />
              </div>
              <p>{GAME_NOTES[game.slug]}</p>
              <b className="dri-game-card-arrow" aria-hidden="true">↗</b>
            </Link>
          ))}

          {riftbound ? (
            <Link
              href="/games/riftbound"
              className="dri-game-card is-soon"
              style={{ '--game-accent': riftbound.accent }}
            >
              <span className="dri-game-card-state">Próximamente</span>
              <div className="dri-game-card-logo">
                <Image src={GAME_LOGOS.riftbound} alt={riftbound.name} width={280} height={100} sizes="190px" />
              </div>
              <p>{GAME_NOTES.riftbound}</p>
            </Link>
          ) : null}
        </div>
      </section>

      <section id="how-it-works" className="dri-home-section app-shell">
        <header className="dri-home-heading">
          <div>
            <span className="v4-overline"><i /> Cómo funciona</span>
            <h2>De lo que recuerdas a la carta exacta, sin rodeos.</h2>
          </div>
          <p>No necesitas conocer antes el ID interno, el finish o la taxonomía del catálogo.</p>
        </header>

        <div className="dri-flow-grid">
          <article className="dri-flow-card">
            <span className="dri-flow-number">01</span>
            <h3>Busca como coleccionista</h3>
            <p>Nombre, número de carta o set. Te mostramos primero resultados entendibles y dejamos los filtros técnicos para cuando realmente hagan falta.</p>
          </article>
          <article className="dri-flow-card">
            <span className="dri-flow-number">02</span>
            <h3>Elige la versión física</h3>
            <p>Arte, set, idioma, rareza, finish, promo o reprint permanecen separados. Ves lo importante antes de guardar nada.</p>
          </article>
          <article className="dri-flow-card">
            <span className="dri-flow-number">03</span>
            <h3>Guárdala desde la misma ficha</h3>
            <p>Precio, colección y wishlist están junto a la versión seleccionada. La ficha avanzada existe para profundizar, no como paso obligatorio.</p>
          </article>
        </div>
      </section>

      <section className="dri-home-section app-shell">
        <header className="dri-home-heading">
          <div>
            <span className="v4-overline"><i /> Datos que puedes confiar</span>
            <h2>Profundo por detrás. Simple por delante.</h2>
          </div>
          <p>Don’tRipIt conserva la complejidad del catálogo sin obligarte a navegar como si estuvieras mirando una base de datos.</p>
        </header>

        <div className="dri-feature-grid">
          <article className="dri-feature-card is-primary">
            <span className="dri-feature-chip">Edición exacta</span>
            <h3>Una carta no es una sola versión.</h3>
            <p>Alternativas, promos, reprints, idiomas y acabados conservan su identidad propia para que tu colección sea realmente precisa.</p>
          </article>
          <article className="dri-feature-card">
            <span className="dri-feature-chip">Mercado</span>
            <h3>Precio con contexto.</h3>
            <p>Cuando existe un precio fiable, mostramos fuente y fecha. Cuando no existe, lo decimos en vez de inventarlo.</p>
          </article>
          <article className="dri-feature-card">
            <span className="dri-feature-chip">Portfolio</span>
            <h3>Cobertura visible.</h3>
            <p>El valor de la colección diferencia lo valorado de lo que todavía no tiene una referencia de mercado verificable.</p>
          </article>
        </div>
      </section>

      <section id="releases" className="dri-home-section app-shell">
        <div className="dri-home-release">
          <div>
            <span className="v4-overline"><i /> Calendario oficial</span>
            <h2>Lo próximo, con región y fuente.</h2>
            <p>Fechas oficiales separadas por mercado. Sin convertir rumores ni calendarios de otra región en datos.</p>
          </div>
          <div className="dri-home-release-list">
            <Link href="/games/pokemon#lanzamientos"><span>POKÉMON</span><strong>Ver próximos lanzamientos</strong><small>USA · EU · JP →</small></Link>
            <Link href="/games/onepiece#lanzamientos"><span>ONE PIECE</span><strong>Ver próximos lanzamientos</strong><small>USA · EU · JP →</small></Link>
            <Link href="/games/magic#lanzamientos"><span>MAGIC</span><strong>Ver próximos lanzamientos</strong><small>USA · EU · JP →</small></Link>
            <Link href="/games/yugioh#lanzamientos"><span>YU-GI-OH!</span><strong>Ver próximos lanzamientos</strong><small>USA · EU · JP →</small></Link>
          </div>
        </div>
      </section>

      <section className="dri-home-final app-shell">
        <div>
          <span className="v4-overline"><i /> Don’tRipIt</span>
          <h2>Busca una carta. Nosotros nos encargamos de que la versión correcta no se pierda por el camino.</h2>
        </div>
        <Link href="/register" className="dri-btn dri-btn-primary">Empezar gratis →</Link>
      </section>

      <SiteFooter />
    </main>
  )
}
