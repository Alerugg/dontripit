import { notFound } from 'next/navigation'
import TopNav from '../../../components/layout/TopNav'
import GameHubPage from '../../../components/games/GameHubPage'
import RiftboundComingSoonPage from '../../../components/games/RiftboundComingSoonPage'
import { getGameConfig } from '../../../lib/catalog/games'

const VALID_VIEWS = new Set(['grid', 'list'])
const VALID_SORTS = new Set(['relevance', 'price_desc', 'price_asc', 'collector_asc', 'collector_desc', 'name_asc', 'name_desc'])
const VALID_LANGUAGES = new Set(['', 'en', 'es', 'ja', 'fr', 'de', 'it', 'pt'])

function normalizeLegacyKind(value) {
  const kind = String(value || '').toLowerCase()
  if (kind === 'card' || kind === 'matches' || kind === 'singles') return 'card'
  if (kind === 'print') return 'print'
  if (kind === 'set' || kind === 'sets') return 'set'
  return ''
}

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

export default async function GamePage({ params, searchParams }) {
  const { slug } = await params
  const query = await searchParams
  const game = getGameConfig(slug)

  if (!game) notFound()

  const initialExplorerState = {
    query: String(query?.q || '').trim(),
    type: normalizeLegacyKind(query?.kind),
    view: VALID_VIEWS.has(query?.view) ? query.view : 'grid',
    sort: VALID_SORTS.has(query?.sort) ? query.sort : 'relevance',
    language: VALID_LANGUAGES.has(query?.language || '') ? (query?.language || '') : '',
    pricedOnly: query?.priced === '1',
  }

  return (
    <main>
      <TopNav />
      {game.slug === 'riftbound'
        ? <RiftboundComingSoonPage game={game} />
        : <GameHubPage game={game} initialExplorerState={initialExplorerState} />}
    </main>
  )
}
