import { notFound, redirect } from 'next/navigation'
import { getGameConfig } from '../../../../lib/catalog/games'

function searchString(searchParams = {}) {
  const next = new URLSearchParams()
  for (const [key, value] of Object.entries(searchParams || {})) {
    if (Array.isArray(value)) {
      value.forEach((item) => next.append(key, String(item)))
    } else if (value !== undefined && value !== null && value !== '') {
      next.set(key, String(value))
    }
  }
  return next.toString()
}

export default async function LegacyGameExplorerPage({ params, searchParams }) {
  const { slug } = await params
  const query = await searchParams
  const game = getGameConfig(String(slug || '').trim().toLowerCase())
  if (!game) notFound()

  const queryString = searchString(query)
  redirect(`/games/${game.slug}${queryString ? `?${queryString}` : ''}`)
}
