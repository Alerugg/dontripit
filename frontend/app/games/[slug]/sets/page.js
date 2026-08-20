import { notFound, redirect } from 'next/navigation'
import TopNav from '../../../../components/layout/TopNav'
import GameCollectionsDirectoryPage from '../../../../components/games/GameCollectionsDirectoryPage'
import { getGameConfig, isGameCatalogActive } from '../../../../lib/catalog/games'

export async function generateMetadata({ params }) {
  const { slug } = await params
  const game = getGameConfig(String(slug || '').toLowerCase())
  if (!game) return {}

  if (!isGameCatalogActive(game.slug)) {
    return {
      title: `${game.name} · Próximamente`,
      description: game.description,
      alternates: { canonical: `/games/${game.slug}` },
      robots: { index: false, follow: true },
    }
  }

  const canonical = `/games/${game.slug}/sets`
  const title = `Sets · ${game.name}`
  const description = `Explora los sets reales de ${game.name}, paginados sobre el catálogo completo de Don’tRipIt.`

  return {
    title,
    description,
    alternates: { canonical },
    openGraph: {
      title: `${title} · Don’tRipIt`,
      description,
      url: canonical,
    },
  }
}

export default async function GameSetsDirectoryRoute({ params }) {
  const { slug: rawSlug } = await params
  const requestedSlug = String(rawSlug || '').trim().toLowerCase()
  const game = getGameConfig(requestedSlug)

  if (!game) notFound()
  if (!isGameCatalogActive(game.slug)) redirect(`/games/${game.slug}`)
  if (requestedSlug !== game.slug) redirect(`/games/${game.slug}/sets`)

  return (
    <main>
      <TopNav />
      <GameCollectionsDirectoryPage game={game} />
    </main>
  )
}
