import { notFound } from 'next/navigation'
import TopNav from '../../../../../components/layout/TopNav'
import SetLandingPage from '../../../../../components/catalog/SetLandingPage'
import { getGameConfig } from '../../../../../lib/catalog/games'

export default function GameSetPage({ params }) {
  const game = getGameConfig(params.slug)

  if (!game) notFound()

  return (
    <main>
      <TopNav />
      <SetLandingPage game={game} setCode={decodeURIComponent(params.setCode)} />
    </main>
  )
}
