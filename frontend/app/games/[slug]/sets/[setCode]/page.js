import { notFound } from 'next/navigation'
import TopNav from '../../../../../components/layout/TopNav'
import GameSetPage from '../../../../../components/games/GameSetPage'
import { getGameConfig } from '../../../../../lib/catalog/games'

export default async function SetPage({ params }) {
  const { slug, setCode } = await params
  const game = getGameConfig(slug)

  if (!game || !setCode) {
    notFound()
  }

  return (
    <main>
      <TopNav />
      <GameSetPage gameSlug={slug} setCode={setCode} />
    </main>
  )
}
