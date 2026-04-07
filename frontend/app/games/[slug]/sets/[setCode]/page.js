import { notFound } from 'next/navigation'
import TopNav from '../../../../../components/layout/TopNav'
import GameSetPage from '../../../../../components/games/GameSetPage'
import { getGameConfig } from '../../../../../lib/catalog/games'

export default function SetPage({ params }) {
  const game = getGameConfig(params.slug)

  if (!game || !params.setCode) {
    notFound()
  }

  return (
    <main>
      <TopNav />
      <GameSetPage gameSlug={params.slug} setCode={params.setCode} />
    </main>
  )
}