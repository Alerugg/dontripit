import Link from 'next/link'
import StatePanel from './StatePanel'
import { getGameExplorerHref } from '../../lib/catalog/routes'

export default function SetLandingPage({ game, setCode }) {
  return (
    <main>
      <section className="detail-shell">
        <Link href={getGameExplorerHref(game.slug)} className="back-link">← Volver al explorer de {game.name}</Link>
        <article className="panel detail-page set-landing-page">
          <div className="detail-content">
            <p className="kicker">Set / colección</p>
            <h1>{setCode}</h1>
            <p className="detail-intro">
              Esta ruta base queda lista para iterar sobre el detalle de colección dentro de {game.name}. Ahora mismo
              actúa como punto de extensión para añadir portada del set, idiomas, bloques destacados y cards lists.
            </p>
            <StatePanel
              title="Ruta preparada para evolucionar"
              description="Puedes colgar aquí detalle del set, filtros propios, variantes destacadas y módulos marketplace sin salir de la arquitectura App Router."
            />
          </div>
        </article>
      </section>
    </main>
  )
}
