import Link from 'next/link'
import { notFound, redirect } from 'next/navigation'
import TopNav from '../../../../components/layout/TopNav'
import OnePieceSearchV2Experience from '../../../../components/searchV2/OnePieceSearchV2Experience'
import { getGameConfig } from '../../../../lib/catalog/games'

function withForcedAdvanced(searchParams = {}) {
  const next = new URLSearchParams()
  for (const [key, value] of Object.entries(searchParams || {})) {
    if (Array.isArray(value)) {
      value.forEach((item) => next.append(key, String(item)))
    } else if (value !== undefined && value !== null && value !== '') {
      next.set(key, String(value))
    }
  }
  next.set('advanced', '1')
  return next
}

export async function generateMetadata({ params }) {
  const { slug } = await params
  const game = getGameConfig(slug)
  if (!game || game.slug === 'riftbound') return {}
  return {
    title: `Búsqueda física avanzada · ${game.name}`,
    description: `Filtra impresiones físicas exactas de ${game.name} por atributos especializados.`,
    robots: { index: false, follow: true },
  }
}

export default async function AdvancedPrintSearchPage({ params, searchParams }) {
  const { slug } = await params
  const query = await searchParams
  const game = getGameConfig(slug)

  if (!game || game.slug === 'riftbound') notFound()

  if (String(query?.advanced || '') !== '1') {
    const next = withForcedAdvanced(query)
    redirect(`/games/${game.slug}/advanced?${next.toString()}`)
  }

  return (
    <main>
      <TopNav />
      <section className="detail-shell">
        <Link href={`/games/${game.slug}#buscar`} className="back-link">← Volver al Explorer de {game.name}</Link>

        <header className="panel-soft" style={{ marginBottom: 18 }}>
          <p className="eyebrow">Herramienta especializada</p>
          <h1 style={{ margin: '8px 0 10px' }}>Filtros físicos avanzados</h1>
          <p className="detail-intro" style={{ margin: 0 }}>
            Usa esta vista cuando necesites rareza, atributos o filtros propios de una impresión física. Para búsquedas normales por carta, set o número, el Explorer del juego sigue siendo el camino principal.
          </p>
        </header>

        <OnePieceSearchV2Experience game={game} />
      </section>
    </main>
  )
}
