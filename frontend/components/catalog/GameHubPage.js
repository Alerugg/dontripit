import Link from 'next/link'
import CatalogExplorer from './CatalogExplorer'
import { getGameExplorerHref } from '../../lib/catalog/routes'

export default function GameHubPage({ game }) {
  return (
    <section className="catalog-shell game-hub-shell">
        <header className="panel game-hub-hero" style={{ '--game-accent': game.accent }}>
          <div className="game-hub-copy">
            <p className="kicker">{game.name} · entrada dedicada</p>
            <h1>{game.name} listo para explorar por sets, cartas y variantes.</h1>
            <p>
              Esta ruta sirve como hub del juego: puedes usarla como base para contenido editorial, destacados,
              accesos rápidos por colección y futuros módulos de marketplace.
            </p>
            <div className="landing-actions">
              <Link href={getGameExplorerHref(game.slug)} className="primary-btn">Abrir explorer</Link>
              <Link href="/" className="secondary-btn">Volver a home</Link>
            </div>
          </div>

          <div className="game-hub-highlights panel-soft">
            <div>
              <span>01</span>
              <strong>Búsqueda scoped</strong>
              <p>Consultas ligeras y relevantes solo dentro del TCG activo.</p>
            </div>
            <div>
              <span>02</span>
              <strong>Sets navegables</strong>
              <p>Preparado para enlazar expansiones, idiomas y bloques promocionales.</p>
            </div>
            <div>
              <span>03</span>
              <strong>Detalle modular</strong>
              <p>Variantes con miniatura, metadatos limpios y hueco para pricing futuro.</p>
            </div>
          </div>
        </header>

        <CatalogExplorer
          scopedGame={game.slug}
          heading={`${game.name} explorer`}
          description={game.description}
          kicker={`Catálogo dedicado · ${game.name}`}
          allowGameSelect={false}
          compactSidebar
        />
    </section>
  )
}
