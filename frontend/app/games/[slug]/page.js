import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameHubPage from '../../../components/games/GameHubPage'
import RiftboundComingSoonPage from '../../../components/games/RiftboundComingSoonPage'
import { getGameConfig } from '../../../lib/catalog/games'

export async function generateMetadata({ params }) {
  const { slug } = await params
  const game = getGameConfig(slug)
  if (!game) return {}

  return {
    title: `${game.name} TCG`,
    description: game.description,
    alternates: { canonical: `/games/${game.slug}` },
    openGraph: {
      title: `${game.name} TCG · Don’tRipIt`,
      description: game.description,
      url: `/games/${game.slug}`,
    },
  }
}

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
