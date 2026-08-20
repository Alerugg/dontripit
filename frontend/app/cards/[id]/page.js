import { notFound, redirect } from 'next/navigation'
import { callInternalApi } from '../../../lib/catalog/internalApi'
import { normalizeGameSlug } from '../../../lib/catalog/games'
import { getCardHref } from '../../../lib/catalog/routes'

function explorerFallback(cardId) {
  const search = new URLSearchParams()
  if (cardId) search.set('q', cardId)
  search.set('kind', 'card')
  search.set('view', 'grid')
  return `/explorer?${search.toString()}`
}

export default async function LegacyCardDetailPage({ params }) {
  const { id } = await params
  const cardId = String(id || '').trim()

  if (!cardId) redirect(explorerFallback(''))

  const upstream = await callInternalApi(`/api/v1/cards/${encodeURIComponent(cardId)}`)

  if (!upstream.ok) {
    if (upstream.status === 404) notFound()
    redirect(explorerFallback(cardId))
  }

  const gameSlug = normalizeGameSlug(upstream.payload?.game_slug || upstream.payload?.game || '')
  if (!gameSlug) redirect(explorerFallback(cardId))

  redirect(getCardHref(gameSlug, cardId))
}
