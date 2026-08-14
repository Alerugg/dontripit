import { callInternalApi } from '../../../lib/catalog/internalApi'
import { getGameConfig, normalizeGameSlug } from '../../../lib/catalog/games'
import { SITE_URL } from '../../../lib/site'

export async function generateMetadata({ params }) {
  const { id } = await params
  const upstream = await callInternalApi(`/api/v1/prints/${id}`)
  const print = upstream.ok ? upstream.payload : null
  const name = print?.card?.name || print?.title || `Versión ${id}`
  const gameSlug = normalizeGameSlug(print?.game || print?.card?.game || '')
  const gameLabel = getGameConfig(gameSlug)?.name || print?.game || 'TCG'
  const identity = [
    print?.set_code ? String(print.set_code).toUpperCase() : null,
    print?.collector_number ? `#${print.collector_number}` : null,
    print?.language ? String(print.language).toUpperCase() : null,
    print?.foil || print?.is_foil ? 'Foil' : null,
  ].filter(Boolean).join(' · ')
  const canonical = `${SITE_URL}/prints/${encodeURIComponent(id)}`
  const title = `${name}${identity ? ` · ${identity}` : ''} · Don’tRipIt`
  const description = `Identifica la versión física exacta de ${name}${identity ? ` (${identity})` : ''} en ${gameLabel}, con su Print ID y referencia Cardmarket cuando existe correspondencia exacta.`

  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title, description, url: canonical },
  }
}

export default function PrintDetailLayoutRoute({ children }) {
  return children
}
