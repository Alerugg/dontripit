import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameHubPage from '../../../components/catalog/GameHubPage'
import { getGameConfig } from '../../../lib/catalog/games'

export default function GamePage({ params }) {
  const game = getGameConfig(params.slug)

  if (!game) notFound()

  return (
    <main>
      <TopNav />
      <GameHubPage game={game} />
    </main>
  )
}
