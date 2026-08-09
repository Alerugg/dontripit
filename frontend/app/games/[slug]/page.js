import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameExplorerPage from '../../../components/games/GameExplorerPage'
import MagicExplorerV2Page from '../../../components/games/MagicExplorerV2Page'
import OnePieceExplorerV2Page from '../../../components/games/OnePieceExplorerV2Page'
import PokemonExplorerV2Page from '../../../components/games/PokemonExplorerV2Page'
import RiftboundComingSoonPage from '../../../components/games/RiftboundComingSoonPage'
import YugiohExplorerV2Page from '../../../components/games/YugiohExplorerV2Page'
import { getGameConfig } from '../../../lib/catalog/games'

export default async function GamePage({ params }) {
  const { slug } = await params
  const game = getGameConfig(slug)

  if (!game) notFound()

  let explorer = <GameExplorerPage game={game} />
  if (game.slug === 'magic') explorer = <MagicExplorerV2Page game={game} />
  if (game.slug === 'onepiece') explorer = <OnePieceExplorerV2Page game={game} />
  if (game.slug === 'pokemon') explorer = <PokemonExplorerV2Page game={game} />
  if (game.slug === 'riftbound') explorer = <RiftboundComingSoonPage game={game} />
  if (game.slug === 'yugioh') explorer = <YugiohExplorerV2Page game={game} />

  return (
    <main>
      <TopNav />
      {explorer}
    </main>
  )
}
