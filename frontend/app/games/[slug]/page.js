import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameExplorerPage from '../../../components/games/GameExplorerPage'
import { getGameConfig } from '../../../lib/catalog/games'

export default function GamePage({ params }) {
  const game = getGameConfig(params.slug)

  if (!game) notFound()

  return (
    <main>
      <TopNav />
      <GameExplorerPage game={game} />
    </main>
  )
}
