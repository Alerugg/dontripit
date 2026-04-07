import { notFound } from 'next/navigation'
import GameCollectionsDirectoryPage from '../../../../components/games/GameCollectionsDirectoryPage'

const GAMES = {
  pokemon: {
    slug: 'pokemon',
    name: 'Pokémon',
    accent: '#9b6bff',
  },
  mtg: {
    slug: 'mtg',
    name: 'Magic',
    accent: '#9b6bff',
  },
  onepiece: {
    slug: 'onepiece',
    name: 'One Piece',
    accent: '#9b6bff',
  },
  yugioh: {
    slug: 'yugioh',
    name: 'Yu-Gi-Oh!',
    accent: '#9b6bff',
  },
  riftbound: {
    slug: 'riftbound',
    name: 'Riftbound',
    accent: '#9b6bff',
  },
}

export default function GameSetsDirectoryRoute({ params }) {
  const slug = String(params?.slug || '').toLowerCase()
  const game = GAMES[slug]

  if (!game) notFound()

  return <GameCollectionsDirectoryPage game={game} />
}