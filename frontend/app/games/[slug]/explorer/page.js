import { notFound } from 'next/navigation'
import TopNav from '../../../../components/layout/TopNav'
import CatalogExplorer from '../../../../components/catalog/CatalogExplorer'
import { getGameConfig } from '../../../../lib/catalog/games'

export default function GameExplorerPage({ params }) {
  const game = getGameConfig(params.slug)

  if (!game) notFound()

  return (
    <main>
      <TopNav />
      <CatalogExplorer
        scopedGame={game.slug}
        heading={`${game.name} explorer`}
        description={game.description}
        kicker={`Exploración por juego · ${game.name}`}
        allowGameSelect={false}
      />
    </main>
  )
}
