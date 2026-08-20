import { cache } from 'react'
import { redirect } from 'next/navigation'
import { callInternalApi } from '../../../lib/catalog/internalApi'
import { getGameConfig, isGameCatalogActive, normalizeGameSlug } from '../../../lib/catalog/games'
import { SITE_URL } from '../../../lib/site'

const loadPrint = cache(async (id) => callInternalApi(`/api/v1/prints/${encodeURIComponent(id)}`))

function printGame(print) {
  const gameSlug = normalizeGameSlug(print?.game || print?.card?.game || '')
  return getGameConfig(gameSlug)
}

export async function generateMetadata({ params }) {
  const { id } = await params
  const upstream = await loadPrint(id)
  const print = upstream.ok ? upstream.payload : null
  const game = printGame(print)

  if (game && !isGameCatalogActive(game.slug)) {
    return {
      title: `${game.name} · Próximamente`,
      description: game.description,
      alternates: { canonical: `/games/${game.slug}` },
      robots: { index: false, follow: true },
    }
  }

  const name = print?.card?.name || print?.title || `Versión ${id}`
  const gameLabel = game?.name || print?.game || 'TCG'
  const identity = [
    print?.set_code ? String(print.set_code).toUpperCase() : null,
    print?.collector_number ? `#${print.collector_number}` : null,
    print?.language ? String(print.language).toUpperCase() : null,
    print?.foil || print?.is_foil ? 'Foil' : null,
  ].filter(Boolean).join(' · ')
  const canonical = `${SITE_URL}/prints/${encodeURIComponent(id)}`
  const title = `${name}${identity ? ` · ${identity}` : ''}`
  const socialTitle = `${title} · Don’tRipIt`
  const description = `Identifica la versión física exacta de ${name}${identity ? ` (${identity})` : ''} en ${gameLabel}, con su Print ID y referencia Cardmarket cuando existe correspondencia exacta.`

  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title: socialTitle, description, url: canonical },
  }
}

export default async function PrintDetailLayoutRoute({ children, params }) {
  const { id } = await params
  const upstream = await loadPrint(id)
  const print = upstream.ok ? upstream.payload : null
  const game = printGame(print)

  if (game && !isGameCatalogActive(game.slug)) redirect(`/games/${game.slug}`)

  return children
}
