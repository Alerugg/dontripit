import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameHubPage from '../../../components/games/GameHubPage'
import RiftboundComingSoonPage from '../../../components/games/RiftboundComingSoonPage'
import { getGameConfig } from '../../../lib/catalog/games'

export default async function GamePage({ params }) {
  const { slug } = await params
  const game = getGameConfig(slug)

  if (!game) notFound()

  return (
    <main>
      <TopNav />
      {game.slug === 'riftbound'
        ? <RiftboundComingSoonPage game={game} />
        : <GameHubPage game={game} />}
    </main>
  )
}
