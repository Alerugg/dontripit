import { notFound, redirect } from 'next/navigation'
import { callInternalApi } from '../../../../../lib/catalog/internalApi'
import { getGameConfig, isGameCatalogActive, normalizeGameSlug } from '../../../../../lib/catalog/games'
import { getCardHref } from '../../../../../lib/catalog/routes'

export async function generateMetadata({ params }) {
  const { slug, cardId } = await params
  const routeGame = getGameConfig(String(slug || '').toLowerCase())

  if (!routeGame || !cardId) return {}
  if (!isGameCatalogActive(routeGame.slug)) {
    return {
      title: `${routeGame.name} · Próximamente`,
      description: routeGame.description,
      alternates: { canonical: `/games/${routeGame.slug}` },
      robots: { index: false, follow: true },
    }
  }

  const upstream = await callInternalApi(`/api/v1/cards/${encodeURIComponent(cardId)}`)
  const card = upstream.ok ? upstream.payload : null
  const payloadGameSlug = normalizeGameSlug(card?.game_slug || card?.game || '')
  const canonicalGame = getGameConfig(payloadGameSlug) || routeGame
  const canonical = getCardHref(canonicalGame.slug, cardId)
  const name = card?.name || `Carta ${cardId}`
  const title = `${name} · ${canonicalGame.name}`
  const description = `Consulta ${name} en ${canonicalGame.name}: carta canónica, impresiones físicas exactas y referencias verificables de mercado en Don’tRipIt.`

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

export default async function GameCardDetailLayout({ children, params }) {
  const { slug, cardId } = await params
  const requestedSlug = String(slug || '').trim().toLowerCase()
  const game = getGameConfig(requestedSlug)

  if (!game || !cardId) notFound()
  if (!isGameCatalogActive(game.slug)) redirect(`/games/${game.slug}`)
  if (requestedSlug !== game.slug) redirect(getCardHref(game.slug, cardId))

  return children
}
