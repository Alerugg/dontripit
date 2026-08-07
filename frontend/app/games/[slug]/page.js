import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameExplorerPage from '../../../components/games/GameExplorerPage'
import OnePieceExplorerV2Page from '../../../components/games/OnePieceExplorerV2Page'
import { getGameConfig } from '../../../lib/catalog/games'

export default function GamePage({ params }) {
  const game = getGameConfig(params.slug)

  if (!game) notFound()

  return (
    <main>
      <TopNav />
      {game.slug === 'onepiece' ? (
        <OnePieceExplorerV2Page game={game} />
      ) : (
        <GameExplorerPage game={game} />
      )}
    </main>
  )
}
