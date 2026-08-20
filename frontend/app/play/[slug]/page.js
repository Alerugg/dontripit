import { notFound, redirect } from 'next/navigation'
import { getGameConfig } from '../../../lib/catalog/games'

export default async function LegacyPlayPage({ params }) {
  const { slug } = await params
  const game = getGameConfig(String(slug || '').trim().toLowerCase())
  if (!game) notFound()
  redirect(`/games/${game.slug}`)
}
