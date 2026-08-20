import { notFound, redirect } from 'next/navigation'
import TopNav from '../../../../../components/layout/TopNav'
import GameSetPage from '../../../../../components/games/GameSetPage'
import { getGameConfig } from '../../../../../lib/catalog/games'

export async function generateMetadata({ params }) {
  const { slug, setCode } = await params
  const game = getGameConfig(slug)
  if (!game || !setCode) return {}

  const normalizedCode = String(setCode).trim().toUpperCase()
  const canonicalPath = `/games/${game.slug}/sets/${encodeURIComponent(String(setCode).trim().toLowerCase())}`
  const title = `${normalizedCode} · ${game.name}`
  const description = `Explora ${normalizedCode} de ${game.name}: cartas, versiones físicas, checklist y referencias verificables de mercado en Don’tRipIt.`

  return {
    title,
    description,
    alternates: { canonical: canonicalPath },
    openGraph: { title: `${title} · Don’tRipIt`, description, url: canonicalPath },
  }
}

export default async function SetPage({ params }) {
  const { slug, setCode } = await params
  const requestedSlug = String(slug || '').trim().toLowerCase()
  const normalizedSetCode = String(setCode || '').trim()
  const game = getGameConfig(requestedSlug)

  if (!game || !normalizedSetCode) {
    notFound()
  }

  if (requestedSlug !== game.slug) {
    redirect(`/games/${game.slug}/sets/${encodeURIComponent(normalizedSetCode.toLowerCase())}`)
  }

  return (
    <main>
      <TopNav />
      <GameSetPage gameSlug={game.slug} setCode={normalizedSetCode} />
    </main>
  )
}
